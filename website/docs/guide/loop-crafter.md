---
hide:
  - toc
---

# Loop crafter

Build the same `problem.yaml` as the [written guide](build-your-own.md) using a form. Choose
the goal and interface, add checks and measurements, set objectives, and configure the flow.
The document preview follows your changes.

Custom measurements can use **+ Dictionary** for a changing set of tests. Give the parent
metric a name, direction and unit, then print JSON such as `timings={"parse": 12, "build": null}`.
Objectives can use a named test, such as `timings.parse`, or the parent aggregate (`timings`).
Aggregation defaults to mean; choose median, min, max, sum or none. Missing values are ignored,
and a group with no finite values stays unmeasured.
Results and Decision offer a test selector and **All** to expand the group. Measurement
visibility is under **Settings → Preferences**, saved for your browser and loop.

Use [Create your first loop](tutorial.md) for the scripts that accompany a complete example,
and the [parameter reference](parameters.md) to understand a field before changing it.
The form writes the document; you still supply any custom scripts, reference models, or data
that its commands name. Loop names use 1–60 letters, digits, hyphens or underscores, in any
position. Save the document as `problem.yaml` inside your loop folder, add those files,
then run `flux task check FOLDER` and a bounded first pass.

<div id="flux-crafter" class="flux-crafter">
  <noscript>The problem builder needs JavaScript. Without it, <a href="../build-your-own/">build your own loop</a> walks through the same file by hand.</noscript>
</div>

<link rel="stylesheet" href="../../assets/crafter.css">
<script src="../../assets/crafter.js"></script>
