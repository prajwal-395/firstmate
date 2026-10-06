# Homebrew tap formula for Ren.
#
# This file is the SOURCE the captain copies into the homebrew-ren tap
# repository before publishing. Per release, the captain updates `url` and
# `sha256` from the release's SHA256SUMS.txt and pushes the tag.
#
# The release artifact is the immutable engine tree that
# .github/workflows/release.yml builds. The formula installs it into the
# Ren home exactly as scripts/install_ren.sh does, so `ren update` and
# `ren rollback` work unchanged. Brew's role is the system dependencies
# (uv, Node.js, ffmpeg) and the `ren` command on PATH.

class Ren < Formula
  desc "Ren: local-first video editing engine (placeholder - captain fills)"
  homepage "https://github.com/prajwal-395/video_editing_pilot"
  url "https://github.com/prajwal-395/video_editing_pilot/releases/download/v0.1.0/ren-engine-v0.1.0.zip"
  # PLACEHOLDER - the captain replaces this with the sha256 of the engine
  # artifact from the release's SHA256SUMS.txt before publishing the tap.
  sha256 "REPLACE_WITH_RELEASE_SHA256"

  # PLACEHOLDER - the captain replaces this with the approved EULA/license
  # before publishing the tap. See docs/legal/REN_EULA_DRAFT.md.
  license "All Rights Reserved"

  livecheck do
    url "https://github.com/prajwal-395/video_editing_pilot/releases"
    strategy :github_latest
  end

  depends_on "uv"
  depends_on "node"
  depends_on "ffmpeg"

  def vep_home
    # The Ren home, resolved exactly as the engine does (ren/engine_root.py).
    if ENV["PIPELINE_VEP_HOME"] && !ENV["PIPELINE_VEP_HOME"].empty?
      Pathname.new(ENV["PIPELINE_VEP_HOME"])
    elsif ENV["XDG_DATA_HOME"] && !ENV["XDG_DATA_HOME"].empty?
      Pathname.new(ENV["XDG_DATA_HOME"]).join("vep")
    else
      Pathname.new(ENV["HOME"]).join(".local", "share", "vep")
    end
  end

  def install
    # The zip holds one top-level ren-engine/ directory: the immutable engine
    # tree. Install it into the Ren home's version store under the same build
    # id the engine computes, then switch `current` onto it.
    staging = Pathname.new(Dir.mktmpdir)
    system "unzip", "-qq", "-o", "ren-engine-v#{version}.zip", "-d", staging
    engine = staging.join("ren-engine")

    engine_version = engine.join("ren/version.py").read[/__version__ = "([^"]+)"/, 1]
    build_py = engine.join("ren/_build.py").read
    sha = build_py[/BUILD_SHA = '([^']+)'/, 1]
    channel = build_py[/BUILD_CHANNEL = '([^']+)'/, 1]
    edition = build_py[/BUILD_EDITION = '([^']+)'/, 1]
    built_at = build_py[/BUILD_DATE = '([^']+)'/, 1]
    build_id = "#{engine_version}+#{sha}.#{channel}.#{edition}.#{built_at}"

    home = vep_home
    version_dir = home.join("versions", build_id)
    odie "Ren #{build_id} is already installed at #{version_dir}" if version_dir.exist?
    FileUtils.mkdir_p(home.join("versions"))
    FileUtils.mv(engine, version_dir)

    current = home.join("current")
    FileUtils.rm_f(current)
    File.symlink(Pathname.new("versions").join(build_id), current)

    # The `ren` command on PATH. The engine resolves its own version from the
    # Ren home's `current` pointer; this shim only puts it on PATH.
    (bin/"ren").write <<~SHIM
      #!/bin/bash
      if [ -n "${PIPELINE_VEP_HOME:-}" ]; then
        VEP_HOME="${PIPELINE_VEP_HOME}"
      elif [ -n "${XDG_DATA_HOME:-}" ]; then
        VEP_HOME="${XDG_DATA_HOME}/vep"
      else
        VEP_HOME="${HOME}/.local/share/vep"
      fi
      exec "${VEP_HOME}/current/bin/ren" "$@"
    SHIM
  end

  def uninstall
    # Remove the engine versions. The runtime (Python, Node, models) and the
    # user's projects stay, exactly as `ren uninstall` keeps them.
    home = vep_home
    FileUtils.rm_rf(home.join("versions"))
    FileUtils.rm_f(home.join("current"))
    FileUtils.rm_f(home.join("previous"))
  end

  def caveats
    <<~EOS
      Ren's engine is installed. Run `ren setup` to install its runtime
      (Python 3.12, Node dependencies, project folders), then `ren doctor`
      to verify the machine.

      Move to a new release with `ren update`; undo it with `ren rollback`.
      The approved EULA and privacy text are the captain's to fill - see
      docs/legal/ in the repository.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/ren --version")
  end
end
