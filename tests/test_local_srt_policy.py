import copy
import json
import subprocess
import sys

import pytest

from matcreator.local_srt_policy import SrtPolicyError, build_policy, policy_digest


@pytest.fixture
def spec():
    return {
        "version": 1, "account_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "home": "/var/lib/matcreator-srt/install/alice/home",
        "state_dir": "/var/lib/matcreator-srt/install/alice/state",
        "policy_path": "/var/lib/matcreator-srt/install/alice/control/policy.json",
        "runtime_root": "/opt/matcreator-srt-runtime/version1",
        "source_root": "/opt/matcreator-srt-runtime/version1/app",
        "python": "/opt/matcreator-srt-runtime/version1/venv/bin/python",
        "bwrap_wrapper_dir": "/opt/matcreator-srt-runtime/version1/bin",
        "node": "/opt/matcreator-srt-tools/node/bin/node",
        "srt_cli": "/opt/matcreator-srt-tools/srt/dist/cli.js",
        "allowed_domains": ["API.Example.COM:443", "*.example.net:443"],
        "denied_domains": ["private.example.net:443"],
        "access_token": "never-include-this-value-in-policy",
    }


def test_policy_defaults_to_private_files_and_exact_network_grants(spec):
    policy = build_policy(spec)
    fs, network = policy["filesystem"], policy["network"]
    assert fs["denyRead"] == ["/"]
    assert fs["allowWrite"] == [spec["home"]]
    assert spec["home"] in fs["allowRead"]
    assert spec["state_dir"] not in fs["allowRead"]
    assert spec["state_dir"] in fs["denyWrite"]
    assert network == {"allowedDomains": ["*.example.net:443", "api.example.com:443"],
                       "deniedDomains": ["private.example.net:443"], "strictAllowlist": True}
    assert "allowAllUnixSockets" not in network
    assert "allowLocalBinding" not in network
    assert spec["access_token"] not in json.dumps(policy)


@pytest.mark.parametrize("grant", ["*", "example.com", "*:443", "example.com:*", "a:0", "a:65536",
                                       "https://example.com:443", "a..b:443", "a:443\n"])
def test_network_permissions_cannot_be_broadened_by_malformed_grants(spec, grant):
    spec["allowed_domains"] = [grant]
    with pytest.raises(SrtPolicyError):
        build_policy(spec)


@pytest.mark.parametrize("field,value", [("home", "/usr/alice"), ("home", "/opt/matcreator-srt-runtime/version1/data"),
                                          ("home", "/tmp/../alice"), ("state_dir", "/var/lib/matcreator-srt/install/alice/home/state"),
                                          ("source_root", "/home/checkout"), ("python", "/usr/bin/python"),
                                          ("node", "/home/alice/node"), ("runtime_root", "/")])
def test_control_and_execution_paths_cannot_overlap_private_data(spec, field, value):
    spec[field] = value
    with pytest.raises(SrtPolicyError):
        build_policy(spec)


@pytest.mark.parametrize("field,value", [("state_dir", "/usr/state"),
    ("policy_path", "/opt/matcreator-srt-runtime/version1/control/policy.json")])
def test_private_control_cannot_be_exposed_by_another_read_grant(spec, field, value):
    spec[field] = value
    with pytest.raises(SrtPolicyError):
        build_policy(spec)


def test_admin_parent_proxy_is_explicit_and_hashed(spec):
    plain = build_policy(spec)
    spec["parent_proxy"] = "http://127.0.0.1:7897"
    proxied = build_policy(spec)
    assert proxied["network"]["parentProxy"] == {"http": spec["parent_proxy"], "https": spec["parent_proxy"]}
    assert policy_digest(plain) != policy_digest(proxied)
    assert policy_digest(proxied) == policy_digest(copy.deepcopy(proxied))


@pytest.mark.parametrize("proxy", ["http://proxy", "file:///tmp/x", "http://proxy:1/path", {"socks": "http://proxy:1"}])
def test_bad_proxy_does_not_silently_disable_filtering(spec, proxy):
    spec["parent_proxy"] = proxy
    with pytest.raises(SrtPolicyError):
        build_policy(spec)


def test_policy_compiler_works_in_isolated_python_without_importing_mc(spec):
    from matcreator import local_srt_policy
    result = subprocess.run([sys.executable, "-I", local_srt_policy.__file__], input=json.dumps(spec),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value == {"policy": build_policy(spec), "policy_sha256": policy_digest(build_policy(spec))}
