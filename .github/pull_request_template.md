**What does this change?**

**Why?**

**How did you check it works?**

---

A few things worth a look before you open it:

- [ ] Comments explain *why*, not what
- [ ] New source files carry the three-line copyright header
- [ ] Anything the assistant says about itself goes through `self._text()`
      rather than hard-coding a name
- [ ] New commands are in `COMMANDS_REFERENCE` in `assistant.py`, which
      is the one list the reference page, the sidebar and the help text
      all read from
