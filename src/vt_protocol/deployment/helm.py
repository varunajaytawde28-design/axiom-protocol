"""On-premise deployment — Helm chart generation for Kubernetes.

Generates a Helm chart structure for deploying VT Protocol to
on-premise Kubernetes clusters.

From SPEC Sprint 15: "On-premise deployment — Helm chart for Kubernetes."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HelmValues:
    """Configurable values for the Helm chart."""

    image_repository: str = "ghcr.io/vt-protocol/vt-protocol"
    image_tag: str = "latest"
    replicas: int = 1
    service_port: int = 8080
    ingress_enabled: bool = False
    ingress_host: str = ""
    persistence_enabled: bool = True
    persistence_size: str = "10Gi"
    persistence_storage_class: str = ""
    resources_cpu_request: str = "100m"
    resources_cpu_limit: str = "500m"
    resources_memory_request: str = "256Mi"
    resources_memory_limit: str = "512Mi"
    env_vars: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "image": {
                "repository": self.image_repository,
                "tag": self.image_tag,
            },
            "replicas": self.replicas,
            "service": {"port": self.service_port},
            "ingress": {
                "enabled": self.ingress_enabled,
                "host": self.ingress_host,
            },
            "persistence": {
                "enabled": self.persistence_enabled,
                "size": self.persistence_size,
                "storageClass": self.persistence_storage_class,
            },
            "resources": {
                "requests": {
                    "cpu": self.resources_cpu_request,
                    "memory": self.resources_memory_request,
                },
                "limits": {
                    "cpu": self.resources_cpu_limit,
                    "memory": self.resources_memory_limit,
                },
            },
            "env": self.env_vars,
        }


@dataclass
class HelmChart:
    """A Helm chart definition."""

    name: str = "vt-protocol"
    version: str = "0.1.0"
    app_version: str = "0.1.0"
    description: str = "VT Protocol — Architecture governance for AI-assisted development"
    values: HelmValues = field(default_factory=HelmValues)

    def chart_yaml(self) -> str:
        """Generate Chart.yaml content."""
        return f"""apiVersion: v2
name: {self.name}
description: {self.description}
type: application
version: {self.version}
appVersion: {self.app_version}
"""

    def values_yaml(self) -> str:
        """Generate values.yaml content."""
        v = self.values
        return f"""# VT Protocol Helm Chart Values
image:
  repository: {v.image_repository}
  tag: {v.image_tag}
  pullPolicy: IfNotPresent

replicas: {v.replicas}

service:
  type: ClusterIP
  port: {v.service_port}

ingress:
  enabled: {str(v.ingress_enabled).lower()}
  host: {v.ingress_host or '""'}

persistence:
  enabled: {str(v.persistence_enabled).lower()}
  size: {v.persistence_size}
  storageClass: {v.persistence_storage_class or '""'}

resources:
  requests:
    cpu: {v.resources_cpu_request}
    memory: {v.resources_memory_request}
  limits:
    cpu: {v.resources_cpu_limit}
    memory: {v.resources_memory_limit}
"""

    def deployment_yaml(self) -> str:
        """Generate deployment template."""
        return """apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-vt-protocol
  labels:
    app: vt-protocol
spec:
  replicas: {{ .Values.replicas }}
  selector:
    matchLabels:
      app: vt-protocol
  template:
    metadata:
      labels:
        app: vt-protocol
    spec:
      containers:
        - name: vt-protocol
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - containerPort: {{ .Values.service.port }}
          resources:
            requests:
              cpu: {{ .Values.resources.requests.cpu }}
              memory: {{ .Values.resources.requests.memory }}
            limits:
              cpu: {{ .Values.resources.limits.cpu }}
              memory: {{ .Values.resources.limits.memory }}
          volumeMounts:
            {{- if .Values.persistence.enabled }}
            - name: data
              mountPath: /data
            {{- end }}
      volumes:
        {{- if .Values.persistence.enabled }}
        - name: data
          persistentVolumeClaim:
            claimName: {{ .Release.Name }}-data
        {{- end }}
"""

    def service_yaml(self) -> str:
        """Generate service template."""
        return """apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-vt-protocol
spec:
  type: {{ .Values.service.type }}
  ports:
    - port: {{ .Values.service.port }}
      targetPort: {{ .Values.service.port }}
  selector:
    app: vt-protocol
"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "app_version": self.app_version,
            "description": self.description,
            "values": self.values.to_dict(),
        }


def generate_helm_chart(
    output_dir: Path,
    *,
    values: HelmValues | None = None,
    chart_name: str = "vt-protocol",
    chart_version: str = "0.1.0",
) -> HelmChart:
    """Generate a complete Helm chart directory structure.

    Creates:
      chart_name/
        Chart.yaml
        values.yaml
        templates/
          deployment.yaml
          service.yaml
    """
    chart = HelmChart(
        name=chart_name,
        version=chart_version,
        values=values or HelmValues(),
    )

    chart_dir = output_dir / chart_name
    templates_dir = chart_dir / "templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    (chart_dir / "Chart.yaml").write_text(chart.chart_yaml())
    (chart_dir / "values.yaml").write_text(chart.values_yaml())
    (templates_dir / "deployment.yaml").write_text(chart.deployment_yaml())
    (templates_dir / "service.yaml").write_text(chart.service_yaml())

    return chart
