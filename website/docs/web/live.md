# Live

A loop's **Live** tab shows what its run does, task by task, as it happens, and what earlier
starts did. It has four views: **Tree**, **Graph**, **Timeline** and **Log**.

![Live's Tree: each pass's tasks, the selected one in the inspector](../assets/web/live-tree-light.webp#only-light)
![Live's Tree: each pass's tasks, the selected one in the inspector](../assets/web/live-tree-dark.webp#only-dark)

## The toolbar

- **Start** picks the start to show: the one running, or an older one, which replays its passes,
  tools and conversations.
- **Pass** shows the current pass, all passes, or one pass (pass 0 is the baseline).
- **Follow** keeps the newest task in view; selecting a task pins it, and **Following**
  resumes.
- **Earlier passes** loads passes before the most recent ones on a long run.

## Tree

One line per task, tinted by its box and named by it (`Search · arch=ripple`,
`Check · golden`), with a spinner while it runs. The steps every pass starts with fold into a
`Setup` line, and finished passes fold.

## Graph

The pass as the loop's drawing: Search, Design, Check, Measure, Choose, and a critic where one
is set. Each box says what it is set to, whether it passed (✓ or ✗), and `×N` when it ran
again. Boxes the loop skipped are dashed. With **All passes**, the chain of passes above the
chosen pass's flow.

![Live's Graph: the pass as the loop's boxes](../assets/web/live-graph-light.webp#only-light)
![Live's Graph: the pass as the loop's boxes](../assets/web/live-graph-dark.webp#only-dark)

## Timeline

The start's time in lanes by box, passes as numbered bands, a run's tries as one bar. Per box:
how many calls, the average, the longest, the total, and its share of the wall clock.

## Log

The loop's log, numbered, problems highlighted, each start marked. Follow it as it grows (it
pauses when you scroll up), filter by text or `/regex/`, show only problems, or download it.

## The inspector

Selecting a task opens it beside the list. A tool's task shows its command, folder, exit and
time, and its output as it runs. An agent's task shows its reply, then its conversation: text,
thinking and tool calls, each input and output. A prompt shows its input while it runs. **Raw**
and **Fullscreen** open the whole of it.

## Older runs

A loop written by an earlier Flux keeps a task journal of an earlier form. Bring it up to date
once with `flux task migrate FOLDER --journal EVENTS`, or on a server with **Admin ›
Maintenance › Bring task journals up to date**. Until then, Live shows its tasks as one setup
pass.
