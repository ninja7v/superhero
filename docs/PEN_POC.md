# Pen "wand": experiment

**Experimental. Not connected to the pointer.** It gets integrated only if it proves reliable.

Run `uv run superhero pen-demo`, with SUPERHERO closed so the camera is free. It shows the
detected pen for 30 seconds, then prints the detection rate and how much the tip jitters.

## How it works

MediaPipe hand landmarks find where the hand grips (between the thumb and index tips). Edge
detection and a Hough transform then look for a long straight line through the grip that sticks
out on the finger side. That last rule is what rejects the forearm. The far end of the line is
taken as the tip, and an affine calibration maps it to the screen.

## Results so far

- Synthetic pens at five angles: tip found within 12 px every time.
- Forearm-like edges and empty hands: correctly rejected.
- **A real pen in a real hand: not tested yet.**

## Known limits

- It tracks where the tip is, not where the pen points. One camera cannot see depth, so it feels
  like moving a marker through the air rather than aiming a wand.
- A pen pointed straight at the camera disappears.
- Any straight edge through the grip counts as a pen: a ruler, a striped sleeve, a door frame.
- Thin, pale or shiny pens, or dim light, can give no edges at all.

## Decision rule

At least 90% detection and no more than 5 px of jitter in ordinary light → integrate it as an
optional pointer. Otherwise, try a trained pen-tip detector, or drop the feature.
