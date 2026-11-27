---
name: adw-config
user-invocable: false
description: Set a project rule gate or open a timed test window.
disable-model-invocation: true
argument-hint: "rule NAME off|observe|enforce or tests allow --for 30m"
---

The host hook handles this user command before the assistant responds. Do not call a tool or edit a file for this command.

If the hook did not report a result, tell the user to check that the plugin hooks are active.
