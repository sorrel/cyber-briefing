"""Tests for collectors.base.host_matches — the domain-boundary URL host check
that replaced the CodeQL-flagged `domain in url` substring tests in the
cloudseclist and TWIS scrapers."""

import pytest

from collectors.base import host_matches


@pytest.mark.parametrize(
    "url",
    [
        "https://cloudseclist.com/issues/issue-345/",
        "https://www.cloudseclist.com/",
        "http://CloudSecList.com/x",  # case-insensitive
        "https://cloudseclist.com:443/x",  # port ignored
    ],
)
def test_matches_domain_and_subdomains(url):
    assert host_matches(url, "cloudseclist.com") is True


@pytest.mark.parametrize(
    "url",
    [
        # the substring-check bypass vectors CodeQL flagged
        "https://cloudseclist.com.evil.com/x",  # attacker subdomain
        "https://notcloudseclist.com/x",  # different registrable domain
        "https://evil.com/?utm_source=cloudseclist.com",  # substring in query
        "https://evil.com/cloudseclist.com",  # substring in path
    ],
)
def test_rejects_lookalike_and_substring_hosts(url):
    assert host_matches(url, "cloudseclist.com") is False


def test_twis_self_link_host():
    assert host_matches("https://this.weekinsecurity.com/p/foo", "this.weekinsecurity.com") is True
    # path/query containing the string must NOT count as a self-link
    assert host_matches("https://evil.com/?x=this.weekinsecurity.com", "this.weekinsecurity.com") is False
