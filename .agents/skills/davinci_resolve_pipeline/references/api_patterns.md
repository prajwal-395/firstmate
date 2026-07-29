# Resolve API Patterns

## Connection

```python
# Use the skill's connect script:
import sys; sys.path.insert(0, ".agents/skills/davinci_resolve_pipeline/scripts")
from connect_resolve import connect
resolve, project, timeline = connect()
```

Or use the MCP server tool: `resolve_control(action="get_status")`

## Page Navigation

```python
resolve.OpenPage("media")    # Media pool
resolve.OpenPage("cut")      # Cut page
resolve.OpenPage("edit")     # Edit page (main)
resolve.OpenPage("fusion")   # Fusion page
resolve.OpenPage("color")    # Color page
resolve.OpenPage("fairlight") # Audio
resolve.OpenPage("deliver")  # Render
```
MCP: `resolve_control(action="open_page", params={"page": "edit"})`

## Timeline Operations

### Get clips on a track
```python
clips = timeline.GetItemListInTrack("video", 1)  # track 1 (1-based)
for clip in clips:
    print(clip.GetName(), clip.GetDuration(), clip.GetStart(), clip.GetEnd())
```
MCP: `timeline(action="get_items", params={"track_type": "video", "track_index": 1})`

### Add tracks
```python
timeline.AddTrack("video")   # adds next video track
timeline.AddTrack("audio")   # adds next audio track
timeline.SetTrackName("video", 2, "B-Roll")
```
MCP: `timeline(action="add_track", params={"track_type": "video"})`

### Place a clip
```python
# Import to media pool first
media_pool = project.GetMediaPool()
clips = media_pool.ImportMedia(["/path/to/clip.mov"])

# Place on timeline
media_pool.AppendToTimeline([{
    "mediaPoolItem": clip,
    "startFrame": 0,
    "endFrame": 90,
    "trackIndex": 1,
    "recordFrame": 0,
}])
```

## Clip Properties

```python
item = clips[0]  # TimelineItem

# Zoom / Pan
item.SetProperty("ZoomX", 1.04)
item.SetProperty("ZoomY", 1.04)
item.SetProperty("Pan", 0.02)    # horizontal offset
item.SetProperty("Tilt", -0.01)  # vertical offset

# Crop (0-100 scale)
item.SetProperty("CropLeft", 5.0)
item.SetProperty("CropRight", 5.0)

# Opacity (0-100)
item.SetProperty("Opacity", 80.0)

# Compositing
item.SetProperty("CompositeMode", 0)  # 0=Normal
```
MCP: `timeline_item(action="set_property", params={"property": "ZoomX", "value": 1.04, ...})`

## Fusion Comp Operations

### Import a .comp file
```python
clip = clips[0]
success = clip.ImportFusionComp("/path/to/effect.comp")
```
MCP: `timeline_item_fusion(action="import_comp", params={"path": "/path/to/effect.comp", "item_index": 0})`

### List comps on a clip
```python
names = clip.GetFusionCompNameList()
comp = clip.GetFusionCompByName(names[0])
```
MCP: `timeline_item_fusion(action="get_comp_names", params={"item_index": 0})`

### Delete a comp
```python
clip.DeleteFusionCompByName("Composition 1")
```
MCP: `timeline_item_fusion(action="delete_comp", params={"name": "Composition 1", "item_index": 0})`

## Render Operations

### Render a single frame to file
```python
resolve.OpenPage("deliver")
project.SetRenderSettings({
    "TargetDir": "/tmp/screenshots",
    "CustomName": "frame_45",
    "FormatWidth": 1080,
    "FormatHeight": 1920,
    "MarkIn": 45,
    "MarkOut": 45,
})
project.AddRenderJob()
project.StartRendering()

# Wait for completion
import time
while project.IsRenderingInProgress():
    time.sleep(0.5)

project.DeleteAllRenderJobs()
resolve.OpenPage("edit")
```
MCP: `render(action="prepare_render_job", params={"target_dir": "/tmp/screenshots", "settings": {"MarkIn": 45, "MarkOut": 45}})`

## Project Settings

```python
project.SetSetting("timelineResolutionWidth", "1080")
project.SetSetting("timelineResolutionHeight", "1920")
project.SetSetting("timelineFrameRate", "30")
```
MCP: `project_settings(action="set", params={"settings": {"timelineResolutionWidth": "1080"}})`

## Audio Track Flooding Prevention

iPhone MOVs have multiple audio streams. To prevent audio flooding:
1. Place V1 clips while only A1 exists
2. Then add A2+ tracks
3. Place music/SFX with `mediaType: 2` (audio only)

```python
# Step 1: Place video clips (only A1 exists)
for clip_info in manifest["clips"]:
    media_pool.AppendToTimeline([{...}])

# Step 2: Add audio tracks
timeline.AddTrack("audio")  # A2
timeline.AddTrack("audio")  # A3

# Step 3: Place music on A2 (audio only)
media_pool.AppendToTimeline([{
    "mediaPoolItem": music_clip,
    "trackIndex": 2,
    "mediaType": 2,  # audio only
}])
```
