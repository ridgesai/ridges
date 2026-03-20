package controller

import (
	"strings"

	ridgesv1alpha1 "github.com/ridgesai/ridges/operator/api/v1alpha1"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/intstr"
)

func buildStatefulSet(ep *ridgesv1alpha1.EvaluatorPool, cfg OperatorConfig) *appsv1.StatefulSet {
	labels := buildLabels(ep)
	replicas := ep.Status.DesiredReplicas
	terminationGrace := int64(900)
	automountSA := false

	priorityClass := "screener-1-priority"
	if ep.Spec.Stage == "screener_2" {
		priorityClass = "screener-2-priority"
	}

	sts := &appsv1.StatefulSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:      ep.Name,
			Namespace: ep.Namespace,
			Labels:    labels,
		},
		Spec: appsv1.StatefulSetSpec{
			Replicas:    &replicas,
			ServiceName: ep.Name,
			Selector: &metav1.LabelSelector{
				MatchLabels: map[string]string{
					"app.kubernetes.io/instance": ep.Name,
				},
			},
			Template: corev1.PodTemplateSpec{
				ObjectMeta: metav1.ObjectMeta{
					Labels: labels,
				},
				Spec: corev1.PodSpec{
					TerminationGracePeriodSeconds: &terminationGrace,
					AutomountServiceAccountToken:  &automountSA,
					PriorityClassName:             priorityClass,
					ImagePullSecrets:              imagePullSecrets(cfg),
					SecurityContext: &corev1.PodSecurityContext{
						FSGroup: int64Ptr(1000),
						SeccompProfile: &corev1.SeccompProfile{
							Type: corev1.SeccompProfileTypeRuntimeDefault,
						},
					},
					NodeSelector: map[string]string{
						"node.cluster.x-k8s.io/ridges-evaluator": "true",
					},
					Containers: []corev1.Container{
						buildScreenerContainer(ep, cfg),
						buildDindContainer(ep, cfg),
					},
					Volumes: []corev1.Volume{
						{
							Name: "workspace",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
								SizeLimit: quantityPtr("10Gi"),
							}},
						},
						{
							Name: "tmp",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
								Medium:    corev1.StorageMediumMemory,
								SizeLimit: quantityPtr("64Mi"),
							}},
						},
						{
							Name: "home",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
								SizeLimit: quantityPtr("64Mi"),
							}},
						},
						{
							Name: "app-logs",
							VolumeSource: corev1.VolumeSource{EmptyDir: &corev1.EmptyDirVolumeSource{
								SizeLimit: quantityPtr("1Gi"),
							}},
						},
					},
				},
			},
			VolumeClaimTemplates: []corev1.PersistentVolumeClaim{
				{
					ObjectMeta: metav1.ObjectMeta{
						Name: "docker-storage",
					},
					Spec: corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{
							corev1.ReadWriteOnce,
						},
						Resources: corev1.VolumeResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceStorage: resource.MustParse("50Gi"),
							},
						},
					},
				},
				{
					ObjectMeta: metav1.ObjectMeta{
						Name: "swebench-repos",
					},
					Spec: corev1.PersistentVolumeClaimSpec{
						AccessModes: []corev1.PersistentVolumeAccessMode{
							corev1.ReadWriteOnce,
						},
						Resources: corev1.VolumeResourceRequirements{
							Requests: corev1.ResourceList{
								corev1.ResourceStorage: resource.MustParse("5Gi"),
							},
						},
					},
				},
			},
		},
	}

	return sts
}

func buildScreenerContainer(ep *ridgesv1alpha1.EvaluatorPool, cfg OperatorConfig) corev1.Container {
	runAsUser := int64(1000)
	allowPrivEsc := false
	readOnlyRoot := true

	envFrom := []corev1.EnvFromSource{
		{
			SecretRef: &corev1.SecretEnvSource{
				LocalObjectReference: corev1.LocalObjectReference{Name: cfg.ScreenerSecretName},
			},
		},
	}

	env := []corev1.EnvVar{
		{Name: "DIND_RESOLVE_GATEWAY", Value: "true"},
		{Name: "MODE", Value: "screener"},
		{Name: "DOCKER_HOST", Value: "tcp://localhost:2375"},
		{Name: "TMPDIR", Value: "/workspace/tmp"},
		{Name: "RIDGES_INFERENCE_GATEWAY_URL", Value: cfg.InferenceGatewayURL},
		{Name: "RIDGES_PLATFORM_URL", Value: cfg.PlatformURL},
	}
	if cfg.CommitHash != "" {
		env = append(env, corev1.EnvVar{Name: "COMMIT_HASH", Value: cfg.CommitHash})
	}

	return corev1.Container{
		Name:            "screener",
		Image:           cfg.ScreenerImage,
		ImagePullPolicy: corev1.PullAlways,
		EnvFrom:         envFrom,
		Env:             env,
		SecurityContext: &corev1.SecurityContext{
			RunAsUser:                &runAsUser,
			AllowPrivilegeEscalation: &allowPrivEsc,
			ReadOnlyRootFilesystem:   &readOnlyRoot,
			Capabilities: &corev1.Capabilities{
				Drop: []corev1.Capability{"ALL"},
			},
		},
		Lifecycle: &corev1.Lifecycle{
			PreStop: &corev1.LifecycleHandler{
				Exec: &corev1.ExecAction{
					Command: []string{"sh", "-c", "sleep 5"},
				},
			},
		},
		Resources: screenerResources(ep),
		VolumeMounts: []corev1.VolumeMount{
			{Name: "workspace", MountPath: "/workspace"},
			{Name: "tmp", MountPath: "/tmp"},
			{Name: "home", MountPath: "/home/screener"},
			{Name: "app-logs", MountPath: "/app/logs"},
			{Name: "swebench-repos", MountPath: "/app/evaluator/datasets/swebench_verified/repos"},
		},
		StartupProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				HTTPGet: &corev1.HTTPGetAction{
					Path: "/readyz",
					Port: intstr.FromInt32(8080),
				},
			},
			FailureThreshold: 360,
			PeriodSeconds:    10,
		},
		ReadinessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				HTTPGet: &corev1.HTTPGetAction{
					Path: "/readyz",
					Port: intstr.FromInt32(8080),
				},
			},
			PeriodSeconds: 10,
		},
		LivenessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				HTTPGet: &corev1.HTTPGetAction{
					Path: "/healthz",
					Port: intstr.FromInt32(8080),
				},
			},
			PeriodSeconds:    30,
			FailureThreshold: 3,
		},
	}
}

func buildDindContainer(ep *ridgesv1alpha1.EvaluatorPool, cfg OperatorConfig) corev1.Container {
	privileged := true

	dindFlags := "--userns-remap=default --host=tcp://127.0.0.1:2375 --storage-driver=overlay2"

	return corev1.Container{
		Name:            "dind",
		Image:           "docker:27-dind",
		ImagePullPolicy: corev1.PullAlways,
		Command: []string{"/bin/sh", "-c", strings.TrimSpace(`
grep -q '^dockremap:' /etc/group  || addgroup -S dockremap
grep -q '^dockremap:' /etc/passwd || adduser -S -G dockremap dockremap
grep -q 'dockremap:100000' /etc/subuid || echo 'dockremap:100000:65536' >> /etc/subuid
grep -q 'dockremap:100000' /etc/subgid || echo 'dockremap:100000:65536' >> /etc/subgid
dockerd-entrypoint.sh dockerd ` + dindFlags + ` &
DPID=$!
trap 'kill -TERM $DPID; wait $DPID' TERM
until docker info >/dev/null 2>&1; do sleep 1; done
iptables -I DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null || iptables-legacy -I DOCKER-USER -d 169.254.169.254/32 -j DROP 2>/dev/null || true
wait $DPID
`)},
		SecurityContext: &corev1.SecurityContext{
			Privileged: &privileged,
		},
		Env: []corev1.EnvVar{
			{Name: "DOCKER_HOST", Value: "tcp://127.0.0.1:2375"},
			{Name: "DOCKER_TLS_CERTDIR", Value: ""},
		},
		Resources: dindResources(ep),
		VolumeMounts: []corev1.VolumeMount{
			{Name: "docker-storage", MountPath: "/var/lib/docker"},
			{Name: "workspace", MountPath: "/workspace"},
		},
		ReadinessProbe: &corev1.Probe{
			ProbeHandler: corev1.ProbeHandler{
				Exec: &corev1.ExecAction{
					Command: []string{"docker", "info"},
				},
			},
			PeriodSeconds:    10,
			FailureThreshold: 30,
		},
	}
}

func buildLabels(ep *ridgesv1alpha1.EvaluatorPool) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":       "ridges-screener",
		"app.kubernetes.io/instance":   ep.Name,
		"app.kubernetes.io/component":  "screener",
		"app.kubernetes.io/part-of":    "ridges",
		"app.kubernetes.io/managed-by": "ridges-operator",
	}
}

func stsName(ep *ridgesv1alpha1.EvaluatorPool) string {
	return ep.Name
}

func screenerResources(ep *ridgesv1alpha1.EvaluatorPool) corev1.ResourceRequirements {
	defaults := corev1.ResourceRequirements{
		Requests: corev1.ResourceList{
			corev1.ResourceCPU:    resource.MustParse("2"),
			corev1.ResourceMemory: resource.MustParse("4Gi"),
		},
		Limits: corev1.ResourceList{
			corev1.ResourceMemory: resource.MustParse("8Gi"),
		},
	}
	if ep.Spec.Resources != nil && ep.Spec.Resources.Screener != nil {
		return *ep.Spec.Resources.Screener
	}
	return defaults
}

func dindResources(ep *ridgesv1alpha1.EvaluatorPool) corev1.ResourceRequirements {
	defaults := corev1.ResourceRequirements{
		Requests: corev1.ResourceList{
			corev1.ResourceCPU:    resource.MustParse("2"),
			corev1.ResourceMemory: resource.MustParse("4Gi"),
		},
		Limits: corev1.ResourceList{
			corev1.ResourceMemory: resource.MustParse("16Gi"),
		},
	}
	if ep.Spec.Resources != nil && ep.Spec.Resources.Dind != nil {
		return *ep.Spec.Resources.Dind
	}
	return defaults
}

func quantityPtr(s string) *resource.Quantity {
	q := resource.MustParse(s)
	return &q
}

func int64Ptr(i int64) *int64 {
	return &i
}

func imagePullSecrets(cfg OperatorConfig) []corev1.LocalObjectReference {
	if cfg.ImagePullSecret == "" {
		return nil
	}
	return []corev1.LocalObjectReference{{Name: cfg.ImagePullSecret}}
}
