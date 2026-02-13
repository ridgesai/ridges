# List of whitelisted validators (including their names and hotkeys)
WHITELISTED_VALIDATORS = [
    {"name": "RoundTable 21",         "short_name": "RT21",     "hotkey": "5Djyacas3eWLPhCKsS3neNSJonzfxJmD3gcrMTFDc4eHsn62"},
    {"name": "Uncle Tao",                                       "hotkey": "5FF1rU17iEYzMYS7V59P6mK2PFtz9wDUoUKrpFd3yw1wBcfq"},
    {"name": "Yuma",                                            "hotkey": "5Eho9y6iF5aTdKS28Awn2pKTd4dFsJ2o3shGtj1vjnLiaKJ1"},
    {"name": "Rizzo",                                           "hotkey": "5GuRsre3hqm6WKWRCqVxXdM4UtGs457nDhPo9F5wvJ16Ys62"},
    {"name": "Ridges",                                          "hotkey": "5GgJptBaUiWwb8SQDinZ9rDQoVw47mgduXaCLHeJGTtA4JMS"},
    {"name": "Crucible Labs",         "short_name": "Crucible", "hotkey": "5HmkM6X1D3W3CuCSPuHhrbYyZNBy2aGAiZy9NczoJmtY25H7"},
    {"name": "tao.bot",                                         "hotkey": "5E2LP6EnZ54m3wS8s1yPvD5c3xo71kQroBw7aUVK32TKeZ5u"},
    {"name": "Opentensor Foundation", "short_name": "OTF",      "hotkey": "5FZ1BFw8eRMAFK5zwJdyefrsn51Lrm217WKbo3MmdFH65YRr"},
    {"name": "Kraken",                                          "hotkey": "5G8iwBWxPjCfu9Fc3jFP37j1Ax5KypDDmUPUSoS9aWAsSCGT"},
    {"name": "TAO.com",                                         "hotkey": "5DP7gcGeTfGfmCzxVNCXQvWBxu58TrrkdikuHQNnGDYb7THU"},
    {"name": "TAOApp",                                          "hotkey": "5F4U4P2j3ctdDS45naSUCxzYAHWTHarsY5JQdcRmMkc8UQZM"},

    # Developer validators, used for testing
    {"name": "Adam's Validator",      "hotkey": "5Dy9FDg5jshHS7MirAFrRsKiFa6GPRMaiHC4Zng4HAgyi8yf"},
    {"name": "Alex's Validator (1)",  "hotkey": "5HpMvcM593HmizCA3ARLNifxjPSLbN3M5RHYy4GiEqmB3x9n"},
    {"name": "Alex's Validator (2)",  "hotkey": "5HNpAXVzWaW4yD9UqH5sXFPt1gPFqNTViDy61NdiViyDQiTQ"},
    {"name": "Alex's Validator (3)",  "hotkey": "5GgqnYQ3QwnCcmxiGatXS3rrHGmkqU3cMSjQFSdLKHDmxyB6"},
    {"name": "Shak's Validator",      "hotkey": "5F26aNVC3rZVNbH36DWdZzxPVH17iBNGD14Wtb4nQem742Q7"},
    {"name": "Abe's Validator",       "hotkey": "5G699LghHWA18yEPq8NpX9gYi8ZDM3fy2BJvSvYWqtt2DHGE"}
]

def is_validator_hotkey_whitelisted(validator_hotkey: str) -> bool:
    return validator_hotkey in [validator["hotkey"] for validator in WHITELISTED_VALIDATORS]

def validator_name_to_hotkey(validator_name: str) -> str:
    return next((validator["hotkey"] for validator in WHITELISTED_VALIDATORS if validator["name"] == validator_name), 'unknown')

def validator_hotkey_to_name(validator_hotkey: str) -> str:
    return next((validator["name"] for validator in WHITELISTED_VALIDATORS if validator["hotkey"] == validator_hotkey), 'unknown')
