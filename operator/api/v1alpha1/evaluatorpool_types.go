package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	ConditionReady          = "Ready"
	ConditionAPIReachable   = "APIReachable"
	ConditionSecretReady    = "SecretReady"
	ConditionNodesAvailable = "NodesAvailable"
	ConditionScalingActive  = "ScalingActive"
	ConditionDegraded       = "Degraded"
)

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status
// +kubebuilder:subresource:scale:specpath=.spec.scaling.minReplicas,statuspath=.status.currentReplicas
// +kubebuilder:resource:shortName=ep,categories={ridges}
// +kubebuilder:printcolumn:name="Stage",type=string,JSONPath=`.spec.stage`
// +kubebuilder:printcolumn:name="Desired",type=integer,JSONPath=`.status.desiredReplicas`
// +kubebuilder:printcolumn:name="Current",type=integer,JSONPath=`.status.currentReplicas`
// +kubebuilder:printcolumn:name="Queue",type=integer,JSONPath=`.status.lastQueueDepth`
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

type EvaluatorPool struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   EvaluatorPoolSpec   `json:"spec,omitempty"`
	Status EvaluatorPoolStatus `json:"status,omitempty"`
}

type EvaluatorPoolSpec struct {
	// +kubebuilder:validation:Enum=screener_1;screener_2
	Stage string `json:"stage"`

	Scaling EvaluatorPoolScaling `json:"scaling"`

	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:default=30
	PollingIntervalSeconds int32 `json:"pollingIntervalSeconds,omitempty"`

	// +optional
	Resources *EvaluatorPoolResources `json:"resources,omitempty"`
}

type EvaluatorPoolResources struct {
	// +optional
	Screener *corev1.ResourceRequirements `json:"screener,omitempty"`
	// +optional
	Dind *corev1.ResourceRequirements `json:"dind,omitempty"`
}

type EvaluatorPoolScaling struct {
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:default=0
	MinReplicas int32 `json:"minReplicas,omitempty"`

	// +kubebuilder:validation:Minimum=1
	MaxReplicas int32 `json:"maxReplicas"`

	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:default=600
	ScaleDownStabilizationSeconds int32 `json:"scaleDownStabilizationSeconds,omitempty"`
}

type EvaluatorPoolStatus struct {
	ObservedGeneration int64        `json:"observedGeneration,omitempty"`
	CurrentReplicas    int32        `json:"currentReplicas"`
	DesiredReplicas    int32        `json:"desiredReplicas"`
	LastQueueDepth     int32        `json:"lastQueueDepth"`
	LastScaleUpTime    *metav1.Time `json:"lastScaleUpTime,omitempty"`
	LastScaleDownTime  *metav1.Time `json:"lastScaleDownTime,omitempty"`
	LastPollTime       *metav1.Time `json:"lastPollTime,omitempty"`

	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true

type EvaluatorPoolList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []EvaluatorPool `json:"items"`
}

func init() {
	SchemeBuilder.Register(&EvaluatorPool{}, &EvaluatorPoolList{})
}
