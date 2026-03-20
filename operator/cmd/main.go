package main

import (
	"flag"
	"os"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	utilruntime "k8s.io/apimachinery/pkg/util/runtime"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	ridgesv1alpha1 "github.com/ridgesai/ridges/operator/api/v1alpha1"
	"github.com/ridgesai/ridges/operator/internal/controller"
)

var scheme = runtime.NewScheme()

func init() {
	utilruntime.Must(clientgoscheme.AddToScheme(scheme))
	utilruntime.Must(ridgesv1alpha1.AddToScheme(scheme))
}

func getNamespace() string {
	if ns := os.Getenv("POD_NAMESPACE"); ns != "" {
		return ns
	}
	if data, err := os.ReadFile("/var/run/secrets/kubernetes.io/serviceaccount/namespace"); err == nil {
		return string(data)
	}
	return "default"
}

func main() {
	var metricsAddr string
	var probeAddr string
	var enableLeaderElection bool

	flag.StringVar(&metricsAddr, "metrics-bind-address", ":8080", "The address the metric endpoint binds to.")
	flag.StringVar(&probeAddr, "health-probe-bind-address", ":8081", "The address the probe endpoint binds to.")
	flag.BoolVar(&enableLeaderElection, "leader-elect", false, "Enable leader election for controller manager.")

	opts := zap.Options{Development: true}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))
	setupLog := ctrl.Log.WithName("setup")

	namespace := getNamespace()
	setupLog.Info("operator namespace resolved", "namespace", namespace)

	required := []string{
		"RIDGES_API_URL", "PLATFORM_URL", "INFERENCE_GATEWAY_URL",
		"SCREENER_IMAGE", "SCREENER_SECRET_NAME",
	}
	for _, key := range required {
		if os.Getenv(key) == "" {
			setupLog.Error(nil, "required env var is missing", "var", key)
			os.Exit(1)
		}
	}

	cfg := controller.OperatorConfig{
		RidgesAPIURL:        os.Getenv("RIDGES_API_URL"),
		PlatformURL:         os.Getenv("PLATFORM_URL"),
		InferenceGatewayURL: os.Getenv("INFERENCE_GATEWAY_URL"),
		ScreenerImage:       os.Getenv("SCREENER_IMAGE"),
		ScreenerSecretName:  os.Getenv("SCREENER_SECRET_NAME"),
		CommitHash:          os.Getenv("COMMIT_HASH"),
		ImagePullSecret:     os.Getenv("IMAGE_PULL_SECRET"),
	}

	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme,
		Metrics: metricsserver.Options{
			BindAddress: metricsAddr,
		},
		HealthProbeBindAddress:  probeAddr,
		LeaderElection:          enableLeaderElection,
		LeaderElectionID:        "ridges-operator.ridges.ai",
		LeaderElectionNamespace: namespace,
		Cache: cache.Options{
			DefaultNamespaces: map[string]cache.Config{
				namespace: {},
			},
			ByObject: map[client.Object]cache.ByObject{
				&corev1.Node{}: {},
			},
		},
	})
	if err != nil {
		setupLog.Error(err, "unable to start manager")
		os.Exit(1)
	}

	if err := (&controller.EvaluatorPoolReconciler{
		Client:   mgr.GetClient(),
		Scheme:   mgr.GetScheme(),
		Config:   cfg,
		Recorder: mgr.GetEventRecorderFor("ridges-operator"),
	}).SetupWithManager(mgr); err != nil {
		setupLog.Error(err, "unable to create controller", "controller", "EvaluatorPool")
		os.Exit(1)
	}

	if err := mgr.AddHealthzCheck("healthz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up health check")
		os.Exit(1)
	}
	if err := mgr.AddReadyzCheck("readyz", healthz.Ping); err != nil {
		setupLog.Error(err, "unable to set up ready check")
		os.Exit(1)
	}

	setupLog.Info("starting manager")
	if err := mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		setupLog.Error(err, "problem running manager")
		os.Exit(1)
	}
}
