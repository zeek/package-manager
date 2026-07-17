"""Unit tests for zeekpkg.package data structures."""

import pytest

from zeekpkg.package import PackageStatus, TrackingMethod


@pytest.mark.parametrize("value", [m.value for m in TrackingMethod])
def test_package_status_coerces_string_tracking_method(value: str) -> None:
    # Manifest JSON deserialises tracking_method as a plain string; verify
    # PackageStatus converts it to the enum.
    status = PackageStatus(tracking_method=value)
    assert status.tracking_method is TrackingMethod(value)


def test_package_status_accepts_enum_tracking_method() -> None:
    status = PackageStatus(tracking_method=TrackingMethod.BRANCH)
    assert status.tracking_method is TrackingMethod.BRANCH


def test_package_status_rejects_invalid_tracking_method() -> None:
    with pytest.raises(ValueError):
        PackageStatus(tracking_method="bogus")
