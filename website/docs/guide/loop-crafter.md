---
hide:
  - navigation
  - toc
---

# Loop crafter

The web app's own configurator, running in this page: build a loop's `problem.yaml` step by
step, with the document beside each step. Nothing leaves your browser; **Download
problem.yaml**, save it in your loop's folder with the scripts it names, then run
`flux task check FOLDER`.

<div id="flux-crafter">
  <noscript>The loop crafter needs JavaScript. Without it, <a href="../build-your-own/">build your own loop</a> walks through the same file by hand.</noscript>
</div>

<link rel="stylesheet" href="../../assets/crafter.css">
<script src="../../assets/crafter-model.js"></script>
<script type="module" src="../../assets/crafter-app.js"></script>

## Next

- The form writes the document; you still supply the scripts, reference models and data its
  commands name. [First loop](tutorial.md) has a complete example.
- Check the loop with `flux task check FOLDER`, then run one pass:
  `flux task run FOLDER --passes 1`.
- [Configure](build-your-own.md) explains each step's choices, and the
  [Document reference](parameters.md) every field. Loop names follow the
  [naming rule](parameters.md#files-and-loading).
- **Save draft** keeps the form (not the files) in this browser. The agents offered here are
  the built-in ones; a server's own agents and shared library appear in the web app only.
