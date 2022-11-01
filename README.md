[![Current Release](https://img.shields.io/github/release/scaarup/aula/all.svg?style=plastic)](https://github.com/scaarup/aula/releases) [![Github All Releases](https://img.shields.io/github/downloads/scaarup/aula/total.svg?style=plastic)](https://github.com/scaarup/aula/releases) [![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=plastic)](https://github.com/scaarup/aula)

# Aula

This is a custom component for Home Assistant to integrate Aula. It is very much based on the great work by @JBoye at https://github.com/JBoye/HA-Aula. However this "rewrite" comes with new features like:

- Installable/updatable via HACS
- School schedules as Home Assistant calendars
- Lots of small fixes and optimizations

## Installation

### HACS

- Add https://github.com/scaarup/aula as a custom repository
- Search for and install the "Aula" integration.
- Restart Home Assistant.

#### Manual installation

- Download the latest release.
- Unpack the release and copy the custom_components/aula directory into the custom_components directory of your Home Assistant installation.
- Restart Home Assistant.

## Setup

- Add the following to your configuration.yaml:

```
aula:
  username: <your unilogin username>
  password: <your unilogin password>
  schoolschedule: true # If you want "skoleskema" as calendars
```

- Restart Home Assistant.