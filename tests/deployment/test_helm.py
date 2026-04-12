"""Tests for Helm chart generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from vt_protocol.deployment.helm import (
    HelmChart,
    HelmValues,
    generate_helm_chart,
)


class TestHelmValues:
    def test_defaults(self) -> None:
        v = HelmValues()
        assert v.replicas == 1
        assert v.service_port == 8080
        assert v.persistence_enabled is True

    def test_to_dict(self) -> None:
        v = HelmValues(replicas=3, service_port=9090)
        d = v.to_dict()
        assert d["replicas"] == 3
        assert d["service"]["port"] == 9090
        assert d["image"]["repository"] == "ghcr.io/vt-protocol/vt-protocol"

    def test_custom_image(self) -> None:
        v = HelmValues(image_repository="custom/image", image_tag="v1.0")
        d = v.to_dict()
        assert d["image"]["repository"] == "custom/image"
        assert d["image"]["tag"] == "v1.0"

    def test_resources(self) -> None:
        v = HelmValues(resources_cpu_limit="1000m", resources_memory_limit="1Gi")
        d = v.to_dict()
        assert d["resources"]["limits"]["cpu"] == "1000m"
        assert d["resources"]["limits"]["memory"] == "1Gi"

    def test_env_vars(self) -> None:
        v = HelmValues(env_vars={"LOG_LEVEL": "debug"})
        d = v.to_dict()
        assert d["env"]["LOG_LEVEL"] == "debug"


class TestHelmChart:
    def test_chart_yaml(self) -> None:
        chart = HelmChart(name="my-app", version="1.0.0")
        yaml = chart.chart_yaml()
        assert "name: my-app" in yaml
        assert "version: 1.0.0" in yaml
        assert "apiVersion: v2" in yaml

    def test_values_yaml(self) -> None:
        chart = HelmChart(values=HelmValues(replicas=2))
        yaml = chart.values_yaml()
        assert "replicas: 2" in yaml
        assert "repository:" in yaml

    def test_deployment_yaml(self) -> None:
        chart = HelmChart()
        yaml = chart.deployment_yaml()
        assert "kind: Deployment" in yaml
        assert "{{ .Values.image.repository }}" in yaml
        assert "{{ .Values.replicas }}" in yaml

    def test_service_yaml(self) -> None:
        chart = HelmChart()
        yaml = chart.service_yaml()
        assert "kind: Service" in yaml
        assert "{{ .Values.service.port }}" in yaml

    def test_to_dict(self) -> None:
        chart = HelmChart()
        d = chart.to_dict()
        assert d["name"] == "vt-protocol"
        assert "values" in d


class TestGenerateHelmChart:
    def test_creates_directory_structure(self, tmp_path: Path) -> None:
        chart = generate_helm_chart(tmp_path)
        chart_dir = tmp_path / "vt-protocol"
        assert chart_dir.is_dir()
        assert (chart_dir / "Chart.yaml").exists()
        assert (chart_dir / "values.yaml").exists()
        assert (chart_dir / "templates" / "deployment.yaml").exists()
        assert (chart_dir / "templates" / "service.yaml").exists()

    def test_chart_yaml_content(self, tmp_path: Path) -> None:
        generate_helm_chart(tmp_path, chart_version="2.0.0")
        content = (tmp_path / "vt-protocol" / "Chart.yaml").read_text()
        assert "version: 2.0.0" in content

    def test_values_yaml_content(self, tmp_path: Path) -> None:
        values = HelmValues(replicas=5, service_port=3000)
        generate_helm_chart(tmp_path, values=values)
        content = (tmp_path / "vt-protocol" / "values.yaml").read_text()
        assert "replicas: 5" in content
        assert "port: 3000" in content

    def test_custom_chart_name(self, tmp_path: Path) -> None:
        generate_helm_chart(tmp_path, chart_name="my-service")
        assert (tmp_path / "my-service" / "Chart.yaml").exists()

    def test_deployment_template(self, tmp_path: Path) -> None:
        generate_helm_chart(tmp_path)
        content = (tmp_path / "vt-protocol" / "templates" / "deployment.yaml").read_text()
        assert "kind: Deployment" in content
        assert "containerPort" in content

    def test_returns_chart_object(self, tmp_path: Path) -> None:
        chart = generate_helm_chart(tmp_path)
        assert chart.name == "vt-protocol"
        assert isinstance(chart, HelmChart)

    def test_idempotent(self, tmp_path: Path) -> None:
        """Running twice shouldn't fail."""
        generate_helm_chart(tmp_path)
        generate_helm_chart(tmp_path)
        assert (tmp_path / "vt-protocol" / "Chart.yaml").exists()
