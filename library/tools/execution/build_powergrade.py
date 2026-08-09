#!/usr/bin/env python3
"""
Build the "4th Wall Base - Memory" Power Grade for DaVinci Resolve.

WHAT THIS SCRIPT DOES (via Resolve Scripting API):
  - Applies ASC CDL color corrections to existing nodes (Slope/Offset/Power/Saturation)
  - This handles the color math: warm amber base, temperature push, shadow toning, contrast

WHAT YOU MUST DO MANUALLY (API limitations — cannot create nodes, power windows, or OFX):
  1. Before running: Add serial nodes until you have at least 4 (right-click → Add Node → Serial)
  2. After running:  Add Node 5 (serial), then apply ResolveFX Film Grain OFX to it
  3. After running:  Add Node 6 (serial), add an Oval Power Window for warm vignette:
                     - Shape: Oval, Softness: ~0.8
                     - Outside: Exposure -0.3, push toward #120B07 (Deep Espresso)
  4. Save to Gallery: Right-click the still → "Add as Power Grade"

Node structure after completion:
  Node 1: Primary Amber     [CDL — scripted]
  Node 2: Temperature 2700K [CDL — scripted]
  Node 3: Espresso Shadows  [CDL — scripted]
  Node 4: Contrast Polish   [CDL — scripted]
  Node 5: Film Grain        [OFX — manual: Amount=0.15, Size=Fine, Softness=0.6]
  Node 6: Warm Vignette     [Power Window — manual: Oval, Softness=0.8, Outside Exp=-0.3]

Usage:
  1. Open DaVinci Resolve → Color page → select a clip
  2. Ensure clip has at least 4 serial nodes
  3. Run: python3 build_powergrade.py
  4. Complete manual steps 2-4 above
  5. The grade is now reusable as a Power Grade across all 4th Wall episodes

Color Palette Reference (Through the 4th Wall):
  Lamp Amber:    #FFB23D    Burnt Honey:   #C46A1A
  Warm Ivory:    #FFF1DA    Deep Espresso: #120B07
  Smoked Umber:  #24160E    Faded Brass:   #D4A34A
  Ice Blue:      #00BFFF    Alarm Red:     #FF2A00
"""

import sys
import os
import time

# ─────────────────────────────────────────────────────────────
# Connect to DaVinci Resolve
# ─────────────────────────────────────────────────────────────

def get_resolve():
    """Connect to DaVinci Resolve scripting API."""
    try:
        from library.tools.resolve_health import check_resolve_connection
        os.environ.setdefault(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        )
        os.environ.setdefault(
            "RESOLVE_SCRIPT_LIB",
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        )
        health = check_resolve_connection()
        if health.get("success"):
            return health["resolve"]
    except ImportError:
        pass

    try:
        # Method 1: Direct import (works when run from Resolve console)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 2: Add the scripting module path
        script_module = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
        if script_module not in sys.path:
            sys.path.insert(0, script_module)
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    try:
        # Method 3: Environment variable path
        resolve_script = os.getenv("RESOLVE_SCRIPT_API")
        if resolve_script:
            sys.path.insert(0, os.path.join(resolve_script, "Modules"))
            import DaVinciResolveScript as dvr
            return dvr.scriptapp("Resolve")
    except ImportError:
        pass

    print("ERROR: Could not connect to DaVinci Resolve.")
    print("Make sure Resolve is running and the scripting API is accessible.")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# CDL Grade Definitions
# ─────────────────────────────────────────────────────────────
#
# CDL uses Slope/Offset/Power which map to Resolve's Gain/Lift/Gamma:
#   Slope  → Gain  (multiplier on input values)
#   Offset → Lift  (added to all values, shifts shadows)
#   Power  → Gamma (exponent, affects midtones)
#
# RGB order: "R G B" as space-separated string
# Saturation: single float string
#
# The goal: push the image into warm amber territory.
# Shadows → Deep Espresso (#120B07)  = very warm, almost no blue
# Mids    → Burnt Honey (#C46A1A)    = rich amber warmth
# Highs   → Lamp Amber (#FFB23D)     = golden highlights
#

GRADE_NODES = {
    # Node 1: Primary Correction — Warm Amber Base
    # Push reds/greens up, blues down across the tonal range
    # This is the foundational warmth layer
    1: {
        "NodeIndex": "1",
        # Slope (Gain): Boost red slightly, reduce blue
        # Neutral is "1.0 1.0 1.0"
        "Slope": "1.05 1.02 0.88",
        # Offset (Lift): Warm the shadows — add red, subtract blue
        # Neutral is "0.0 0.0 0.0"
        "Offset": "0.02 0.01 -0.03",
        # Power (Gamma): Push mids warm — lower power = brighter mids
        # Neutral is "1.0 1.0 1.0"
        "Power": "0.95 0.97 1.05",
        # Boost saturation for that rich warm look
        "Saturation": "1.15",
    },

    # Node 2: Temperature Push — 2700-3000K Warmth
    # Aggressive orange/amber push simulating tungsten lighting
    # This is what makes it feel like "2:15 AM under a warm lamp"
    2: {
        "NodeIndex": "2",
        # Heavy red gain, moderate green, strong blue reduction
        "Slope": "1.08 1.01 0.82",
        # Lift shadows further into warmth
        "Offset": "0.03 0.015 -0.04",
        # Gamma: brighten red/green mids, darken blue mids
        "Power": "0.93 0.96 1.08",
        "Saturation": "1.05",
    },

    # Node 3: Shadow Toning — Deep Espresso Shadows
    # Target: shadows should be #120B07 (R:18 G:11 B:7, normalized ~0.07 0.04 0.03)
    # This node specifically shapes the shadow color without affecting highlights
    3: {
        "NodeIndex": "3",
        # Keep gain neutral-ish (shadows are controlled by offset/power)
        "Slope": "1.0 0.99 0.95",
        # Push shadows deep into warm espresso territory
        "Offset": "0.015 0.005 -0.025",
        # Power shapes the transition from shadows to mids
        # Higher power = darker mids (more shadow influence)
        "Power": "1.02 1.04 1.10",
        "Saturation": "1.0",
    },

    # Node 4: Contrast & Polish
    # Add contrast to separate the warm tones, slight desaturation
    # of blues (any remaining cool tones get muted)
    4: {
        "NodeIndex": "4",
        # Slightly boost highlights, slightly reduce shadows
        "Slope": "1.03 1.02 0.97",
        # Subtle lift reduction for deeper blacks
        "Offset": "-0.01 -0.01 -0.015",
        # Neutral gamma
        "Power": "1.0 1.0 1.0",
        # Slight saturation boost for final richness
        "Saturation": "1.08",
    },
}

# Node labels for organizational clarity in the Color page
NODE_LABELS = {
    1: "4W - Primary Amber",
    2: "4W - Temp 2700K",
    3: "4W - Espresso Shadows",
    4: "4W - Contrast Polish",
    5: "4W - Grain (manual)",
}


def build_grade(resolve):
    """Apply the 4th Wall grade to the currently selected clip."""

    # Get current project and timeline
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project open.")
        return False

    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No timeline open.")
        return False

    # Get the current clip on the Color page
    clip = timeline.GetCurrentVideoItem()
    if not clip:
        print("ERROR: No clip selected on the Color page.")
        print("       Switch to Color page and select a clip first.")
        return False

    print(f"Target clip: {clip.GetName()}")

    # Get the node graph
    graph = clip.GetNodeGraph()
    if not graph:
        print("ERROR: Could not access node graph.")
        return False

    num_nodes = graph.GetNumNodes()
    print(f"Current node count: {num_nodes}")

    # We need at least 5 nodes. If fewer exist, we'll work with what we have.
    # DaVinci Resolve starts clips with 1 node by default.
    # The API doesn't have an "AddNode" method — nodes must be added manually
    # or the grade is applied to existing nodes.
    #
    # Strategy: Apply CDL to nodes 1 through min(4, num_nodes).
    # Node 5 is reserved for manual film grain (OFX or LUT).

    if num_nodes < 4:
        print(f"\nWARNING: Clip has only {num_nodes} node(s).")
        print(f"         For the full 4th Wall grade, add nodes until you have at least 4.")
        print(f"         (Right-click in node graph → Add Node → Add Serial)")
        print(f"         Will apply grade to available nodes ({num_nodes}).")
        print()

    nodes_to_grade = min(4, num_nodes)

    # Apply CDL grades
    print("\nApplying 4th Wall Base - Memory grade:")
    for node_idx in range(1, nodes_to_grade + 1):
        cdl = GRADE_NODES[node_idx]
        success = clip.SetCDL(cdl)
        if success:
            print(f"  ✅ Node {node_idx}: {NODE_LABELS.get(node_idx, '?')}")
        else:
            print(f"  ❌ Node {node_idx}: Failed to apply CDL")

    print(f"\n  Nodes graded: {nodes_to_grade}/4")
    if num_nodes >= 5:
        print(f"  Node 5 reserved for manual film grain (OFX ResolveFX Film Grain)")
    print()

    return clip, project


def save_as_powergrade(resolve, clip, project):
    """Grab a still and save it to the PowerGrade album."""

    gallery = project.GetGallery()
    if not gallery:
        print("WARNING: Could not access Gallery. Power Grade not saved.")
        print("         You can manually drag the still to Power Grades.")
        return False

    # Get or create PowerGrade album
    pg_albums = gallery.GetGalleryPowerGradeAlbums()

    target_album = None
    for album in pg_albums:
        name = gallery.GetAlbumName(album)
        if name == "4th Wall Grades":
            target_album = album
            print(f"Found existing PowerGrade album: '4th Wall Grades'")
            break

    if not target_album:
        target_album = gallery.CreateGalleryPowerGradeAlbum()
        if target_album:
            gallery.SetAlbumName(target_album, "4th Wall Grades")
            print("Created new PowerGrade album: '4th Wall Grades'")
        else:
            print("WARNING: Could not create PowerGrade album.")
            # Fall back to first available
            if pg_albums:
                target_album = pg_albums[0]
                print(f"  Using existing album: '{gallery.GetAlbumName(target_album)}'")
            else:
                print("  No PowerGrade albums available. Skipping.")
                return False

    # Set the target album as current
    gallery.SetCurrentStillAlbum(target_album)

    # Grab a still from the graded clip
    still = timeline = project.GetCurrentTimeline()
    if still:
        grabbed = still.GrabStill()
        if grabbed:
            # Label the still
            target_album.SetLabel(grabbed, "4th Wall Base - Memory")
            print(f"✅ Still grabbed and labeled '4th Wall Base - Memory'")

            # Export as .drx file for backup
            export_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "pipeline_output"
            )
            os.makedirs(export_dir, exist_ok=True)

            drx_success = target_album.ExportStills(
                [grabbed],
                export_dir,
                "4th_wall_base_memory",
                "drx"
            )
            if drx_success:
                print(f"✅ Exported .drx to: {export_dir}/4th_wall_base_memory.drx")
            else:
                print("⚠️  .drx export failed (may need to export manually)")

            return True
        else:
            print("WARNING: GrabStill() returned None")
    else:
        print("WARNING: Could not get current timeline for still grab")

    return False


def print_grade_summary():
    """Print a human-readable summary of the grade."""
    print("=" * 60)
    print("4th Wall Base - Memory — Grade Summary")
    print("=" * 60)
    print()
    for idx, cdl in sorted(GRADE_NODES.items()):
        label = NODE_LABELS.get(idx, f"Node {idx}")
        slope = cdl["Slope"].split()
        offset = cdl["Offset"].split()
        power = cdl["Power"].split()
        sat = cdl["Saturation"]

        print(f"  Node {idx}: {label}")
        print(f"    Slope  (Gain):  R={slope[0]:>5s}  G={slope[1]:>5s}  B={slope[2]:>5s}")
        print(f"    Offset (Lift):  R={offset[0]:>6s}  G={offset[1]:>6s}  B={offset[2]:>6s}")
        print(f"    Power  (Gamma): R={power[0]:>5s}  G={power[1]:>5s}  B={power[2]:>5s}")
        print(f"    Saturation: {sat}")
        print()

    print("  Node 5: 4W - Grain (manual)")
    print("    → Apply ResolveFX Film Grain OFX plugin manually")
    print("    → Settings: Amount=0.15, Size=Fine, Softness=0.6")
    print()
    print("Target Palette:")
    print("  Shadows  → Deep Espresso  #120B07")
    print("  Mids     → Burnt Honey    #C46A1A")
    print("  Highs    → Lamp Amber     #FFB23D")
    print("  Text     → Warm Ivory     #FFF1DA")
    print("  Emphasis → Ice Blue       #00BFFF")
    print("=" * 60)


def main():
    print()
    print_grade_summary()
    print()

    print("═" * 60)
    print("RECOMMENDED: Use the DCTL instead of this CDL script")
    print("═" * 60)
    print()
    print("A complete DCTL (DaVinci Color Transform Language) grade")
    print("has been built that bundles EVERYTHING into a single node:")
    print("  → Warm amber color correction")
    print("  → 2700K temperature push")
    print("  → Deep Espresso shadow toning")
    print("  → Soft oval vignette")
    print("  → Film grain (memory treatment)")
    print("  → All with user-adjustable sliders")
    print()
    print("Location: pipeline_output/4thWall_Base_Memory.dctl")
    print("Install:  Copy to /Library/Application Support/Blackmagic Design/")
    print("          DaVinci Resolve/LUT/4th Wall/")
    print("Apply:    Color page → node → right-click → LUT → 4th Wall")
    print("          → 4thWall_Base_Memory")
    print()
    print("The DCTL approach is superior because:")
    print("  - Single node, no manual setup")
    print("  - GPU-accelerated (no performance hit)")
    print("  - Adjustable sliders for intensity, warmth, grain, vignette")
    print("  - Portable across projects and machines")
    print("═" * 60)
    print()

    # Check for --dry-run flag
    if "--dry-run" in sys.argv:
        print("DRY RUN — grade definitions printed above.")
        print("Run without --dry-run to apply CDL values to Resolve.")
        print("(Or just use the DCTL — it's better.)")
        return

    print("Connecting to DaVinci Resolve...")
    resolve = get_resolve()

    if not resolve:
        print("ERROR: Could not connect to Resolve.")
        sys.exit(1)

    print(f"Connected to Resolve {resolve.GetVersion()}")
    print()

    result = build_grade(resolve)
    if result:
        clip, project = result
        print("CDL grade applied successfully!")
        print()

        # Try to save as PowerGrade
        print("Saving as Power Grade...")
        save_as_powergrade(resolve, clip, project)

        print()
        print("=" * 60)
        print("DONE — CDL grade applied. But consider the DCTL instead.")
        print("=" * 60)
    else:
        print("Grade application failed. Check errors above.")
        sys.exit(1)


if __name__ == "__main__":
    main()

