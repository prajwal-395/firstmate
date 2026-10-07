"""The launchd agent contract: where it runs, where it logs, that it restarts.

Defect caught: an agent that runs on login but never comes back from a
crash, logs where nobody looks, or points at a disposable worktree that
is gone by the time it restarts. Install itself is manual (only after
the delivering PR merges), so this pins the plist RENDER, not the
machine's launchd state.
"""

from library.tools.local_ci import config, service


def test_plist_restarts_logs_and_names_the_service():
    text = service.render_plist("/repo/checkout")
    assert f"<string>{service.LABEL}</string>" in text
    assert "<key>KeepAlive</key><true/>" in text
    assert "<key>RunAtLoad</key><true/>" in text
    assert f"{config.log_dir()}/daemon.log" in text
    assert f"{config.log_dir()}/daemon.err" in text


def test_plist_points_at_the_repo_checkout_not_a_worktree():
    text = service.render_plist("/repo/checkout")
    assert "/repo/checkout/bin/vep" in text
    assert "library.tools.local_ci" in text
    assert "daemon" in text


def test_agent_lives_in_the_user_domain_with_machine_state():
    assert service.plist_path().endswith(
        "Library/LaunchAgents/com.ren.ci.plist")
    assert config.state_dir().startswith("/"), "state must be absolute"
    assert "video-editing-pilot" not in config.state_dir(), (
        "daemon state must not live inside any repo checkout")
