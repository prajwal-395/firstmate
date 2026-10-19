# Resolve Workflow Integration route evaluation

Reviewed 2026-10-19 on the installed Blackmagic Workflow Integrations developer package and the installed `com.videoeditingpilot.vep` plugin.

## What the route provides

The installed developer README describes Workflow Integration plugins as Resolve Studio hosted Electron apps (macOS and Windows; not Linux) that call Resolve's scriptable JavaScript API through `WorkflowIntegration.node`. Resolve also launches Python or Lua integration scripts. The plugin module offers initialization, access to the Resolve object, callbacks for render start/stop and Resolve quit, cleanup, and API timeout controls. Resolve 20.1 adds Promise-based asynchronous calls; this changes how calls are awaited, not which editing operations exist. The installed README explicitly says JavaScript API use is in the plugin HTML environment and is not supported from a console. Sources: the installed package at `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/README.txt` and `CHANGELOG.txt`; Blackmagic's [DaVinci Resolve 20.1 New Features Guide](https://documents.blackmagicdesign.com/SupportNotes/DaVinci_Resolve_20.1_New_Features_Guide.pdf?_v=1756105210000).

The sample scripts demonstrate common scripting API actions such as media-pool operations, timeline markers, and rendering. They do not add a distinct editing API or stronger operation-specific targeting, read-back, recovery, or performance evidence than the existing AXI scripting/native routes. No speed advantage is inferred from the Promise API; no comparable edit measurement was found.

## Installed VEP plugin

The installed `com.videoeditingpilot.vep` version 0.1 is a read-only panel. Its `js/readonly.js` is a default-refuse method allowlist and separately names mutation methods it withholds. Every Resolve call in `main.js` goes through that guard. `preload.js` exposes only context, video-item reads, API-surface inspection, a Python bridge, file stat, diagnostics, repository-root, and local media URL helpers. There is no edit-request submission, mutation IPC, execution receipt, or recovery channel. The installed bridge invokes `library.tools.workflow_bridge`, which is absent from this checkout, so that bridge is not a current integration into this repository either.

## Disposition

Do not register Workflow Integration as a mutation backend for the 1.2 operation list. The plugin host is an interface around the existing Resolve scripting API, not a new operation route. Plugin-owned writes would bypass AXI's single-instance broker/lease, operation qualification, authorization, receipt, and recovery path. The installed VEP plugin intentionally refuses those writes. Revisit only if a future integration submits semantic EditRequests through AXI and returns AXI's verification and recovery receipts; the plugin itself must not become a second Resolve writer.

This evaluation does not qualify or enable any operation. Live Resolve qualification remains **built, run deferred** under the captain's no-local-testing rule.

## Evidence paths

- Installed host contract: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/README.txt`
- Installed host changes: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Workflow Integrations/CHANGELOG.txt`
- Installed plugin allowlist: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.videoeditingpilot.vep/js/readonly.js`
- Installed plugin Resolve access and guard: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.videoeditingpilot.vep/main.js`
- Installed renderer bridge surface: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Workflow Integration Plugins/com.videoeditingpilot.vep/preload.js`
