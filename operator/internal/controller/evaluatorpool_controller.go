package controller

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"time"

	ridgesv1alpha1 "github.com/ridgesai/ridges/operator/api/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	policyv1 "k8s.io/api/policy/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/metrics"

	"github.com/prometheus/client_golang/prometheus"
)

// OperatorConfig holds operator-level env-var configuration.
type OperatorConfig struct {
	RidgesAPIURL        string
	PlatformURL         string
	InferenceGatewayURL string
	ScreenerImage       string
	ScreenerSecretName  string
	CommitHash          string // optional: screener code commit passed to screener pods
	ImagePullSecret     string // optional: secret name for pulling images from a private registry
}

// +kubebuilder:rbac:groups=ridges.ai,resources=evaluatorpools,verbs=get;list;watch
// +kubebuilder:rbac:groups=ridges.ai,resources=evaluatorpools/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=ridges.ai,resources=evaluatorpools/scale,verbs=get;update;patch
// +kubebuilder:rbac:groups=apps,resources=statefulsets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=nodes,verbs=list
// +kubebuilder:rbac:groups=policy,resources=poddisruptionbudgets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=networking.k8s.io,resources=networkpolicies,verbs=get;list;watch;create;update;patch;delete

type EvaluatorPoolReconciler struct {
	client.Client
	Scheme   *runtime.Scheme
	Config   OperatorConfig
	Recorder record.EventRecorder
}

var (
	metricQueueDepth = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "evaluatorpool_queue_depth",
		Help: "Current queue depth per EvaluatorPool",
	}, []string{"evaluatorpool"})

	metricDesiredReplicas = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "evaluatorpool_desired_replicas",
		Help: "Desired replicas per EvaluatorPool",
	}, []string{"evaluatorpool"})

	metricPreflightStatus = prometheus.NewGaugeVec(prometheus.GaugeOpts{
		Name: "evaluatorpool_preflight_status",
		Help: "Preflight check status (1=ok, 0=fail) per EvaluatorPool and condition",
	}, []string{"evaluatorpool", "condition"})

	metricDriftCorrections = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "evaluatorpool_drift_corrections_total",
		Help: "Total drift corrections applied",
	}, []string{"evaluatorpool"})

	metricScalingErrors = prometheus.NewCounterVec(prometheus.CounterOpts{
		Name: "evaluatorpool_scaling_errors_total",
		Help: "Total scaling errors",
	}, []string{"evaluatorpool"})
)

func init() {
	metrics.Registry.MustRegister(
		metricQueueDepth,
		metricDesiredReplicas,
		metricPreflightStatus,
		metricDriftCorrections,
		metricScalingErrors,
	)
}

func (r *EvaluatorPoolReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	var ep ridgesv1alpha1.EvaluatorPool
	if err := r.Get(ctx, req.NamespacedName, &ep); err != nil {
		if apierrors.IsNotFound(err) {
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, err
	}

	allPreflightOK := r.runPreflightChecks(ctx, &ep)

	desired := buildStatefulSet(&ep, r.Config)
	if err := controllerutil.SetControllerReference(&ep, desired, r.Scheme); err != nil {
		return ctrl.Result{}, fmt.Errorf("setting owner ref on StatefulSet: %w", err)
	}

	desiredHash := podSpecHash(desired.Spec.Template.Spec)
	if desired.Spec.Template.Annotations == nil {
		desired.Spec.Template.Annotations = map[string]string{}
	}
	desired.Spec.Template.Annotations["ridges.ai/pod-spec-hash"] = desiredHash

	var existing appsv1.StatefulSet
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, &existing)
	if apierrors.IsNotFound(err) {
		logger.Info("creating StatefulSet", "name", desired.Name)
		if err := r.Create(ctx, desired); err != nil {
			return ctrl.Result{}, fmt.Errorf("creating StatefulSet: %w", err)
		}
		r.Recorder.Eventf(&ep, corev1.EventTypeNormal, "Created", "Created StatefulSet %s", desired.Name)
	} else if err != nil {
		return ctrl.Result{}, fmt.Errorf("getting StatefulSet: %w", err)
	} else {
		existingHash := existing.Spec.Template.Annotations["ridges.ai/pod-spec-hash"]
		replicasDrift := existing.Spec.Replicas == nil || *existing.Spec.Replicas != *desired.Spec.Replicas

		if existingHash != desiredHash || replicasDrift {
			existing.Spec.Replicas = desired.Spec.Replicas
			existing.Spec.Template = desired.Spec.Template
			if err := r.Update(ctx, &existing); err != nil {
				return ctrl.Result{}, fmt.Errorf("updating StatefulSet: %w", err)
			}
			logger.Info("drift corrected on StatefulSet", "name", desired.Name,
				"hashChanged", existingHash != desiredHash, "replicasDrift", replicasDrift)
			r.Recorder.Eventf(&ep, corev1.EventTypeWarning, "DriftCorrected", "StatefulSet %s was out of spec", desired.Name)
			metricDriftCorrections.WithLabelValues(ep.Name).Inc()
		}
	}

	if err := r.ensurePDB(ctx, &ep); err != nil {
		logger.Error(err, "ensuring PDB")
	}

	if err := r.ensureNetworkPolicy(ctx, &ep); err != nil {
		logger.Error(err, "ensuring NetworkPolicy")
	}

	if allPreflightOK {
		queueDepth, activeCount, err := r.fetchQueueDepth(ctx, &ep)
		if err != nil {
			logger.Error(err, "fetching queue depth")
			r.Recorder.Eventf(&ep, corev1.EventTypeWarning, "QueueDepthError", "Failed to fetch queue depth: %v", err)
			metricScalingErrors.WithLabelValues(ep.Name).Inc()
		} else {
			ep.Status.LastQueueDepth = queueDepth
			ep.Status.LastPollTime = nowTime()
			metricQueueDepth.WithLabelValues(ep.Name).Set(float64(queueDepth))

			newDesired := r.computeDesiredReplicas(ctx, &ep, queueDepth, activeCount)
			oldDesired := ep.Status.DesiredReplicas

			if newDesired != oldDesired {
				if newDesired > oldDesired {
					r.Recorder.Eventf(&ep, corev1.EventTypeNormal, "ScaleUp",
						"Scaling from %d to %d (queue depth: %d, active: %d)", oldDesired, newDesired, queueDepth, activeCount)
					ep.Status.LastScaleUpTime = nowTime()
				} else {
					r.Recorder.Eventf(&ep, corev1.EventTypeNormal, "ScaleDown",
						"Scaling from %d to %d (queue depth: %d, active: %d)", oldDesired, newDesired, queueDepth, activeCount)
					ep.Status.LastScaleDownTime = nowTime()
				}
				ep.Status.DesiredReplicas = newDesired
			}
			metricDesiredReplicas.WithLabelValues(ep.Name).Set(float64(newDesired))

			meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
				Type:               ridgesv1alpha1.ConditionScalingActive,
				Status:             metav1.ConditionTrue,
				ObservedGeneration: ep.Generation,
				Reason:             "Polling",
				Message:            fmt.Sprintf("Queue depth: %d, active: %d, desired replicas: %d", queueDepth, activeCount, ep.Status.DesiredReplicas),
			})
		}
	}

	var latestSTS appsv1.StatefulSet
	if err := r.Get(ctx, types.NamespacedName{Name: ep.Name, Namespace: ep.Namespace}, &latestSTS); err == nil {
		ep.Status.CurrentReplicas = latestSTS.Status.ReadyReplicas
	}

	ready := allPreflightOK && ep.Status.CurrentReplicas > 0
	readyStatus := metav1.ConditionFalse
	readyReason := "NotReady"
	readyMessage := "One or more preflight checks failed or no ready replicas"
	if ready {
		readyStatus = metav1.ConditionTrue
		readyReason = "AllChecksPass"
		readyMessage = "All preflight checks pass and replicas are ready"
	}
	meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
		Type:               ridgesv1alpha1.ConditionReady,
		Status:             readyStatus,
		ObservedGeneration: ep.Generation,
		Reason:             readyReason,
		Message:            readyMessage,
	})

	ep.Status.ObservedGeneration = ep.Generation
	if err := r.Status().Update(ctx, &ep); err != nil {
		return ctrl.Result{}, fmt.Errorf("updating EvaluatorPool status: %w", err)
	}

	requeue := time.Duration(ep.Spec.PollingIntervalSeconds) * time.Second
	return ctrl.Result{RequeueAfter: requeue}, nil
}

func (r *EvaluatorPoolReconciler) runPreflightChecks(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) bool {
	allOK := true

	secretOK := r.checkSecret(ctx, ep)
	if !secretOK {
		allOK = false
	}
	metricPreflightStatus.WithLabelValues(ep.Name, ridgesv1alpha1.ConditionSecretReady).Set(boolToFloat(secretOK))

	apiOK := r.checkAPIReachable(ctx, ep)
	if !apiOK {
		allOK = false
	}
	metricPreflightStatus.WithLabelValues(ep.Name, ridgesv1alpha1.ConditionAPIReachable).Set(boolToFloat(apiOK))

	nodesOK := r.checkNodesAvailable(ctx, ep)
	if !nodesOK {
		allOK = false
	}
	metricPreflightStatus.WithLabelValues(ep.Name, ridgesv1alpha1.ConditionNodesAvailable).Set(boolToFloat(nodesOK))

	return allOK
}

func (r *EvaluatorPoolReconciler) checkSecret(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) bool {
	var secret corev1.Secret
	err := r.Get(ctx, types.NamespacedName{Name: r.Config.ScreenerSecretName, Namespace: ep.Namespace}, &secret)
	if err != nil {
		meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
			Type:               ridgesv1alpha1.ConditionSecretReady,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: ep.Generation,
			Reason:             "SecretMissing",
			Message: fmt.Sprintf("Secret %q not found. Create it from validator/.env: make dev-secrets -n %s",
				r.Config.ScreenerSecretName, ep.Namespace),
		})
		r.Recorder.Eventf(ep, corev1.EventTypeWarning, "PreflightFailed", "Secret %s not found", r.Config.ScreenerSecretName)
		return false
	}

	if _, ok := secret.Data["SCREENER_PASSWORD"]; !ok {
		meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
			Type:               ridgesv1alpha1.ConditionSecretReady,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: ep.Generation,
			Reason:             "SecretKeyMissing",
			Message:            fmt.Sprintf("Secret %q exists but missing 'SCREENER_PASSWORD' key", r.Config.ScreenerSecretName),
		})
		return false
	}

	meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
		Type:               ridgesv1alpha1.ConditionSecretReady,
		Status:             metav1.ConditionTrue,
		ObservedGeneration: ep.Generation,
		Reason:             "SecretFound",
		Message:            "Secret exists with required keys",
	})
	return true
}

func (r *EvaluatorPoolReconciler) checkAPIReachable(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) bool {
	url := fmt.Sprintf("%s/screener/queue-depth?stage=%s", r.Config.RidgesAPIURL, ep.Spec.Stage)
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
			Type:               ridgesv1alpha1.ConditionAPIReachable,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: ep.Generation,
			Reason:             "Unreachable",
			Message:            fmt.Sprintf("Cannot reach Ridges API at %s: %v. Check RIDGES_API_URL and network.", r.Config.RidgesAPIURL, err),
		})
		r.Recorder.Eventf(ep, corev1.EventTypeWarning, "PreflightFailed", "Ridges API unreachable: %v", err)
		return false
	}
	resp.Body.Close()

	meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
		Type:               ridgesv1alpha1.ConditionAPIReachable,
		Status:             metav1.ConditionTrue,
		ObservedGeneration: ep.Generation,
		Reason:             "Reachable",
		Message:            "Ridges API is reachable",
	})
	return true
}

func (r *EvaluatorPoolReconciler) checkNodesAvailable(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) bool {
	var nodes corev1.NodeList
	if err := r.List(ctx, &nodes, client.MatchingLabels{"node.cluster.x-k8s.io/ridges-evaluator": "true"}); err != nil {
		meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
			Type:               ridgesv1alpha1.ConditionNodesAvailable,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: ep.Generation,
			Reason:             "ListError",
			Message:            fmt.Sprintf("Failed to list nodes: %v", err),
		})
		return false
	}

	if len(nodes.Items) == 0 {
		meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
			Type:               ridgesv1alpha1.ConditionNodesAvailable,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: ep.Generation,
			Reason:             "NoNodes",
			Message:            "No nodes with label node.cluster.x-k8s.io/ridges-evaluator=true. Label a node: kubectl label node <name> node.cluster.x-k8s.io/ridges-evaluator=true",
		})
		r.Recorder.Eventf(ep, corev1.EventTypeWarning, "PreflightFailed", "No DinD-capable nodes found")
		return false
	}

	meta.SetStatusCondition(&ep.Status.Conditions, metav1.Condition{
		Type:               ridgesv1alpha1.ConditionNodesAvailable,
		Status:             metav1.ConditionTrue,
		ObservedGeneration: ep.Generation,
		Reason:             "NodesFound",
		Message:            fmt.Sprintf("%d DinD-capable node(s) available", len(nodes.Items)),
	})
	return true
}

func (r *EvaluatorPoolReconciler) ensurePDB(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) error {
	desired := &policyv1.PodDisruptionBudget{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ep.Name,
			Namespace: ep.Namespace,
			Labels:    buildLabels(ep),
		},
		Spec: policyv1.PodDisruptionBudgetSpec{
			MaxUnavailable: intstrPtr(1),
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app.kubernetes.io/instance": ep.Name,
				},
			},
		},
	}

	if err := controllerutil.SetControllerReference(ep, desired, r.Scheme); err != nil {
		return err
	}

	var existing policyv1.PodDisruptionBudget
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, &existing)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, desired)
	} else if err != nil {
		return err
	}

	existing.Spec = desired.Spec
	return r.Update(ctx, &existing)
}

func (r *EvaluatorPoolReconciler) ensureNetworkPolicy(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) error {
	dnsPort := intstr.FromInt32(53)
	httpPort := intstr.FromInt32(80)
	httpsPort := intstr.FromInt32(443)
	apiPort := intstr.FromInt32(8000)
	gatewayPort := intstr.FromInt32(8080)
	udp := corev1.ProtocolUDP
	tcp := corev1.ProtocolTCP

	desired := &networkingv1.NetworkPolicy{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ep.Name,
			Namespace: ep.Namespace,
			Labels:    buildLabels(ep),
		},
		Spec: networkingv1.NetworkPolicySpec{
			PodSelector: metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app.kubernetes.io/instance": ep.Name,
				},
			},
			PolicyTypes: []networkingv1.PolicyType{networkingv1.PolicyTypeEgress},
			Egress: []networkingv1.NetworkPolicyEgressRule{
				{
					Ports: []networkingv1.NetworkPolicyPort{
						{Port: &dnsPort, Protocol: &udp},
					},
				},
				{
					Ports: []networkingv1.NetworkPolicyPort{
						{Port: &httpPort, Protocol: &tcp},
						{Port: &httpsPort, Protocol: &tcp},
					},
				},
				{
					Ports: []networkingv1.NetworkPolicyPort{
						{Port: &apiPort, Protocol: &tcp},
						{Port: &gatewayPort, Protocol: &tcp},
					},
					To: []networkingv1.NetworkPolicyPeer{
						{
							NamespaceSelector: &metav1.LabelSelector{
								MatchLabels: map[string]string{
									"kubernetes.io/metadata.name": ep.Namespace,
								},
							},
						},
					},
				},
			},
		},
	}

	if err := controllerutil.SetControllerReference(ep, desired, r.Scheme); err != nil {
		return err
	}

	var existing networkingv1.NetworkPolicy
	err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, &existing)
	if apierrors.IsNotFound(err) {
		return r.Create(ctx, desired)
	} else if err != nil {
		return err
	}

	existing.Spec = desired.Spec
	return r.Update(ctx, &existing)
}

func (r *EvaluatorPoolReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&ridgesv1alpha1.EvaluatorPool{}).
		Owns(&appsv1.StatefulSet{}).
		Owns(&policyv1.PodDisruptionBudget{}).
		Owns(&networkingv1.NetworkPolicy{}).
		Complete(r)
}

func intstrPtr(val int32) *intstr.IntOrString {
	v := intstr.FromInt32(val)
	return &v
}

func boolToFloat(b bool) float64 {
	if b {
		return 1
	}
	return 0
}

func podSpecHash(spec corev1.PodSpec) string {
	data, _ := json.Marshal(spec)
	sum := sha256.Sum256(data)
	return fmt.Sprintf("%x", sum)[:16]
}
