---
hide:
  - navigation
  - toc
---

# Loop crafter

Build the same `problem.yaml` as the [written guide](build-your-own.md) using a form: this is the
web app's own configurator, the one **New loop › Configurator** opens, in six steps. **Prompt**
names the loop and says what to make; **Check & Measure** adds the checks and measurements;
**Objective** says what is better; **Graph** draws the loop on its circle, where each box says who
works it (rules, the model, a coding agent, a script) and **Knowledge** what every agent and prompt
reads; **Extra** holds the settings search, parts and a baseline; **Save** says what is left to do.
The document beside each step follows your changes, the step's lines lit.

A check's **Type** and a measurement's **Tool** include **Flux presets**: choose it, then the
preset from the second list (RTL lint and golden test, OpenROAD-flow-scripts stages, program
timing, ChampSim). Presets are Flux's own commands, so the loop's folder needs no script for them.

Custom measurements can use **+ Dictionary** for a changing set of tests. Give the parent
metric a name, direction and unit, then print JSON such as `timings={"parse": 12, "build": null}`.
Objectives can use a named test, such as `timings.parse`, or the parent aggregate (`timings`).
Aggregation defaults to mean; choose median, min, max, sum or none. Missing values are ignored,
and a group with no finite values stays unmeasured.
Results and Decision offer a test selector and **All** to expand the group. Measurement
visibility is under **Settings → Preferences**, saved for your browser and loop.

Here there is no server: files you write or drop under **Prompt › Files** stay in this page, so
the configurator can say which files the document names and lacks, and **Save draft** keeps the
form (not the files) in this browser. **Download problem.yaml** (on every step, or **Copy** and
the download button beside the document) saves the document. The agents offered are the built-in
ones (Claude Code, Codex, OpenCode); a server's own agents and its shared library appear in the
web app only.

Use [Create your first loop](tutorial.md) for the scripts that accompany a complete example,
and the [parameter reference](parameters.md) to understand a field before changing it.
The form writes the document; you still supply any custom scripts, reference models, or data
that its commands name. Loop names use 1–60 letters, digits, hyphens or underscores, in any
position. Save the document as `problem.yaml` inside your loop folder, add those files,
then run `flux task check FOLDER` and a bounded first pass.

<div id="flux-crafter">
  <noscript>The loop crafter needs JavaScript. Without it, <a href="../build-your-own/">build your own loop</a> walks through the same file by hand.</noscript>
</div>

<link rel="stylesheet" href="../../assets/crafter.css">
<script src="../../assets/crafter-model.js"></script>
<script type="module" src="../../assets/crafter-app.js"></script>
