package controller

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	ridgesv1alpha1 "github.com/ridgesai/ridges/operator/api/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

type queueDepthResponse struct {
	Depth  int32  `json:"depth"`
	Stage  string `json:"stage"`
	Active int32  `json:"active"`
}

func (r *EvaluatorPoolReconciler) fetchQueueDepth(ctx context.Context, ep *ridgesv1alpha1.EvaluatorPool) (int32, int32, error) {
	url := fmt.Sprintf("%s/screener/queue-depth?stage=%s", r.Config.RidgesAPIURL, ep.Spec.Stage)

	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		return 0, 0, fmt.Errorf("GET %s: %w", url, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return 0, 0, fmt.Errorf("GET %s returned %d: %s", url, resp.StatusCode, string(body))
	}

	var qd queueDepthResponse
	if err := json.NewDecoder(resp.Body).Decode(&qd); err != nil {
		return 0, 0, fmt.Errorf("decoding queue depth response: %w", err)
	}

	return qd.Depth, qd.Active, nil
}

func (r *EvaluatorPoolReconciler) computeDesiredReplicas(
	ctx context.Context,
	ep *ridgesv1alpha1.EvaluatorPool,
	queueDepth int32,
	activeCount int32,
) int32 {
	logger := log.FromContext(ctx)
	min := ep.Spec.Scaling.MinReplicas
	max := ep.Spec.Scaling.MaxReplicas

	// Queue depth = agents waiting for a screener.
	// Active count = evaluations currently being processed (1 per screener).
	// Total demand = waiting + in-progress.
	desired := queueDepth + activeCount
	if desired < min {
		desired = min
	}
	if desired > max {
		desired = max
	}

	current := ep.Status.DesiredReplicas

	if desired < current {
		if activeCount > 0 {
			logger.V(1).Info("scale-down blocked: evaluations in progress",
				"activeCount", activeCount,
				"keeping", current,
			)
			return current
		}

		stabilization := time.Duration(ep.Spec.Scaling.ScaleDownStabilizationSeconds) * time.Second

		if ep.Status.LastScaleUpTime != nil {
			if elapsed := time.Since(ep.Status.LastScaleUpTime.Time); elapsed < stabilization {
				logger.V(1).Info("scale-down blocked: recent scale-up",
					"elapsed", elapsed,
					"stabilization", stabilization,
					"keeping", current,
				)
				return current
			}
		}

		if ep.Status.LastScaleDownTime != nil {
			if elapsed := time.Since(ep.Status.LastScaleDownTime.Time); elapsed < stabilization {
				logger.V(1).Info("scale-down stabilization active",
					"elapsed", elapsed,
					"stabilization", stabilization,
					"keeping", current,
				)
				return current
			}
		}

		if desired < current-1 {
			desired = current - 1
		}
	}

	return desired
}

func nowTime() *metav1.Time {
	t := metav1.Now()
	return &t
}
