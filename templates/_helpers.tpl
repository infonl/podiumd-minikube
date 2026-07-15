{{/*
Common labels applied to every raw resource this chart defines itself
(not the podiumd dependency's own resources, which use their own helpers).
*/}}
{{- define "podiumd-minikube.labels" -}}
app.kubernetes.io/part-of: podiumd-minikube
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
