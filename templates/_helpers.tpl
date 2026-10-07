{{/*
Common labels applied to every raw resource this chart defines itself
(not the podiumd dependency's own resources, which use their own helpers).
*/}}
{{- define "podiumd-minikube.labels" -}}
app.kubernetes.io/part-of: podiumd-minikube
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
{{/*
A Traefik Ingress from host to a service; dict with root, name, host,
service and optional port (80).
*/}}
{{- define "podiumd-minikube.ingress" -}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {{ .name }}
  labels:
    {{- include "podiumd-minikube.labels" .root | nindent 4 }}
spec:
  ingressClassName: traefik
  rules:
    - host: {{ .host }}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: {{ .service }}
                port:
                  number: {{ .port | default 80 }}
{{- end -}}
{{/*
The vendored WireMock mapping sets to load, space-separated: KvK and BAG
behind the api-proxy always, SmartDocuments with the wiremock profile.
*/}}
{{- define "podiumd-minikube.wiremockSets" -}}
kvk-wiremock bag-wiremock{{ if .Values.wiremock.enabled }} smartdocuments-wiremock{{ end }}
{{- end -}}
