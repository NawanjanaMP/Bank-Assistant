{{- define "ba.labels" -}}
app.kubernetes.io/part-of: bank-assistant
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- define "ba.securityContext" -}}
runAsNonRoot: true
runAsUser: 10001
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: { drop: [ALL] }
seccompProfile: { type: RuntimeDefault }
{{- end }}
