# ZTF_ToO_toolkit
Central repository for scripts handling ZTF neutrino Target of Opportunity requests

[![Binder](https://mybinder.org/badge_logo.svg)](https://mybinder.org/v2/gh/robertdstein/ZTF_Neutrino_ToO/master)

## Installation Instructions

The majority of required packages can be installed with the command:

```pip install -r requirements.txt```

You will need the , as well as IRSA login details with a ZTF-enabled account, to fully utilise all features.

## Connecting to ampel

There are additional requirements to access ampel. To do so, you need to be a member of the ampel github group. Then you can additionally run:

```pip install -r ampel_requirements.txt```

In order to use the ampel tunnel, you need to have a separate shell that opens tunnels to the ampel database. You can do this using the following command in a terminal:

```ssh -L5432:localhost:5433 -L27020:localhost:27020 -L27018:localhost:27018 -L27026:localhost:27026 ztf-wgs.zeuthen.desy.de
```

However, you need to make sure that your ~/.ssh/config file is configured directly for this to work.
