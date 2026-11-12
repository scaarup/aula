[![Current Release](https://img.shields.io/github/release/scaarup/aula/all.svg?style=plastic)](https://github.com/scaarup/aula/releases) [![Github All Releases](https://img.shields.io/github/downloads/scaarup/aula/total.svg?style=plastic)](https://github.com/scaarup/aula/releases) [![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg?style=plastic)](https://github.com/hacs/integration)

# Aula

This is a custom component for Home Assistant to integrate Aula.

- Installable and updatable via HACS
- UI config flow
- School schedules as Home Assistant calendars
- Booked meetings (skole-hjem-samtaler, forældresamtaler) as Home Assistant calendars (opt-in)
- "Ugeplaner/Ugenoter" from "Min Uddannelse", "Meebook", "EasyIQ" and "Docendo"
- "Opgaver" from "Min Uddannelse", including the assignment notes/descriptions (`indhold`). The sensor exposes them as HTML in `mu_opgaver` / `mu_opgaver_next` and as structured lists in `mu_opgaver_data` / `mu_opgaver_next_data` (`id`, `titel`, `dato`, `ugedag`, `fag`, `indhold`). Notes are fetched from the Min Uddannelse web API for the current and next ISO week; if that fails, the assignments are still shown without notes.
- Messages - if there are unread messages, we turn a binary sensor on and populate it with the message details.
- "Huskelisten" from "Systematic"
- Use the builtin service to interact directly with Aulas API.

  "Ugeplaner/ugenoter/huskelisten" are stored as sensor attributes. Can be rendered like:

  ```yaml
  {{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}
  ```

  And visualized in your dashboard with the markdown card:

  ```yaml
  type: markdown
  content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}'
  title: Ugeplan for Emilie
  ```

  Another example using vertical-stack and collapsable-cards:

  ![image](https://user-images.githubusercontent.com/8055470/200306258-1c9e98ff-75d9-4111-994c-a69833e40c61.png)

```yaml
type: vertical-stack
cards:
  - type: custom:collapsable-cards
    title: Ugeplan Emilie
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Emilie, næste uge
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_emilie", "ugeplan_next") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Rasmus
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_rasmus", "ugeplan") }}'
  - type: custom:collapsable-cards
    title: Ugeplan Rasmus, næste uge
    cards:
      - type: markdown
        content: '{{ state_attr("sensor.hojelse_skole_rasmus", "ugeplan_next") }}'
```

   ![image](https://user-images.githubusercontent.com/8055470/199254249-3bf441bc-7dce-4f5d-a809-d119d20a7b2b.png)

- Lots of small fixes and optimizations

## Installation

#### HACS

- Ensure that HACS is installed.
- Search for and install the "Aula" integration.
- Restart Home Assistant.

#### Manual installation

- Download the latest release.
- Unpack the release and copy the custom_components/aula directory into the custom_components directory of your Home Assistant installation.
- Restart Home Assistant.

## Setup

Shortcut:<br>
[![](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=aula)

- Go to Settings -> Integrations -> Add Integration
- Search for "Aula" and follow the instructions in the config flow.

### Booked meetings (samtaler)

Enable "booked meetings" when adding the integration, or later under the integration's
**Configure** options. Each child then gets a `calendar.samtaler_<name>` calendar with
the time you booked for every skole-hjem-samtale and forældresamtale in Aula — only
your own booked slot, not the whole booking period, and not other families' slots.
Events are titled with the child's first name, e.g. "Emilie: Skole-hjem-samtale".
Meetings are looked up 100 days ahead and refreshed every 30 minutes. A meeting you have
not booked a time for yet does not appear.

### Ugeplaner (weekly plans)

Enable "ugeplaner" when adding the integration, or later under **Configure**. The
integration picks the ugeplan source from the widgets your school has enabled in Aula:

| Source | Aula widget |
|---|---|
| Min Uddannelse | 0029 / 0023 |
| Meebook | 0004 |
| EasyIQ | 0001 / 0128 / 0142 |
| Docendo | 0057 |

This week's and next week's plan are stored in the `ugeplan` and `ugeplan_next`
attributes of the child's sensor. For EasyIQ and Docendo, lessons are also added to the
child's "Ugeplan <name>" calendar.

**Docendo:** Docendo returns one weekplan per class. The plan is matched to a child by the
class shown in Aula (e.g. "4I"), ignoring case, spaces and punctuation. With one child, an
unmatched plan is still shown; with several children, a plan that matches none of their
classes is skipped (logged at debug level). Docendo plans are refreshed every 30 minutes.

**EasyIQ "Generelt om ugen":** On EasyIQ SkolePortal (widget 0128), the teachers' weekly
letter ("Generelt om ugen") is put at the top of `ugeplan` / `ugeplan_next` under its own
heading. It is also added to the "Ugeplan <name>" calendar as an all-day event, Monday to
Friday, with the letter as plain text in the description. Weeks without a letter are unchanged.

> Ugeplaner are read through the same unofficial widget endpoints that Aula's own web
> page uses, and can change without notice. If an update breaks them, you can go back to
> the previous version in HACS (Aula → ⋮ → Redownload → pick the version).

### Known issues
- You must use the guardian MitID, childlogin is not supported.
- The config flow does not currently support a reconfiguration. Meaning when your password expires, the integration must be deleted and added again, in order to update the password.

## Support
Join our Discord https://discord.gg/SnfRg3DWG6 and feel free to ask in #homeassistant
