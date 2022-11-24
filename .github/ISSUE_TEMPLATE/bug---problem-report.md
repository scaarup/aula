---
name: Bug / problem report
about: Report a problem so we can improve the integration
title: ''
labels: bug
assignees: scaarup

---

**Describe the bug or problem**
<!--
  A clear and concise description of what the bug is.
-->

**Please answer the following**
- [ ] I have more than one child
- [ ] My children are attending different schools / institutions

**Please provide debug log from the integration**
- Enable by adding the following to your configuration.yaml:
```
logger:
  default: info
  logs:
    custom_components.aula: debug
```
- Restart Home Assistant
- Capture all log lines (from the integration only), save it to a file and attach it to here.
