# Camera

Camera moves in Ren's Graphics IR are transforms on a `Camera` node
that wraps the scene. The camera is a virtual 2D/3D viewport, not a
physical camera. All moves are authored geometry - there is no
automatic face tracking or semantic reframing.

## The mechanism

A camera move is a `translate` + `scale` tween on a wrapper element
that contains the scene content. To zoom into a point that is not
the frame centre:

- Scale the outer wrapper.
- Counter-translate the inner wrapper by `T = -offset x (scale - 1)`.

The counter-translation is what keeps the target point centred while
the rest of the frame scales around it.

## Move types

| Move | Mechanism | Use |
|---|---|---|
| Punch-in / punch-out | `scale` tween on a wrapper | hard zoom, short |
| Smooth zoom / reframe | `scale` + `translate` with per-segment easing | multi-state zoom |
| Pan / Ken Burns | `translate` + `scale` on a wrapper | slow reveal of a still |
| Pull-back | `scale` from 1 to <1 | reveal context |
| Push | `scale` from <1 to 1 | dive into detail |

## Rules

- **One subject wrapper alive.** Keep one element alive across the
  move and author multiple zoom/reframe states as a pose ladder
  with per-segment easing. Do not crossfade between two copies of
  the subject - the identity break reads as a cut.
- **Constant-speed path travel uses `ease: "none"`.** Any easing on a
  path move reads as a hitch.
- **Discrete states use a sharp ease-out.** A UI state change
  (locked, unlocked) should snap, not glide.
- **Final lockups need longer holds than transition poses.** The
  viewer needs to read the final state.
- **Do not overlap tweens that write the same transform property.**
  Unless the overlap is intentional and verified, the result is
  undefined.
- **Avoid animating large `clip-path`/mask changes while the same
  surface is also scaling.** Use nested reveals after the main move
  settles.

## Ken Burns

A `translate` + `scale` on a still image. Set its amount and duration
from the requested result and image; use a direction that reveals the
subject. Do not apply a generic scale or timing range.

## Depth

Scale alone is fake depth. Real depth needs:

- `perspective` on a stable parent.
- `transform-style: preserve-3d` on the world.
- Z travel, rotation, or camera/world motion.
- Occlusion and layer order when objects cross.

If an angled proof shows no depth crossing, the move is a 2D scale
wearing a 3D costume. Add z/camera travel or drop the 3D claim.
