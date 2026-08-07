# Latest MDC2025 datasets

Latest dsconf per description across the three main tiers. Generated 2026-08-06.

| tier | descriptions | pattern |
|---|---:|---|
| dig | 51 | `dig.mu2e.%.MDC2025%best%.art` |
| mcs | 63 | `mcs.mu2e.%.MDC2025%best%.art` |
| nts | 46 | `nts.mu2e.%.MDC2025%best%.root` |

## Scope and method

Two exclusions, both deliberate:

- **`Triggered` variants** — filtered with `grep -v Trig`.
- **The `MDC2025-NNN` ntuple series** — superseded, excluded via the
  `%best%` term, which no `MDC2025-NNN` dsconf contains. This drops 16
  `nts` descriptions, all of them `*-reco-ntuple`, `ensembleMDS3c`, or
  `ensembleMDS3cNtuple`. Verified it changes no winner: of the 46
  descriptions present either way, every one keeps the same latest
  dsconf.

All three tiers use the default `--latest-by dsconf` (lexicographic).
That is correct here **because** `MDC2025-NNN` is excluded — with both
series in play, `MDC2025-002` sorts below `MDC2025au_best_v1_5` (`-` is
0x2D, `a` is 0x61) and dsconf order returns stale rows. If you ever
re-include that series, switch to `--latest-by time`.

**versions** is the `--show-count` column: how many dsconf versions exist
for the description, the listed one being newest. `1` = produced once and
never revised. `--superseded` prints the older versions themselves.

---

# dig

```bash
python3 bin/latestDatasets --defname 'dig.mu2e.%.MDC2025%best%.art' --show-count | grep -v Trig
```

### Campaign spread

| dsconf | datasets |
|---|---:|
| `MDC2025au_best_v1_5` | 22 |
| `MDC2025au_best_v1_3` | 19 |
| `MDC2025an_best_v1_1` | 5 |
| `MDC2025ai_best_v1_3` | 2 |
| `MDC2025ap_best_v1_1` | 2 |
| `MDC2025au_best_v1_1` | 1 |

### Datasets (51)

| description | dsconf | versions |
|---|---|---:|
| CeEndpointOnSpill | `MDC2025an_best_v1_1` | 1 |
| CeMLeadingLogMix1BB | `MDC2025au_best_v1_3` | 2 |
| CeMLeadingLogOnSpill | `MDC2025au_best_v1_5` | 3 |
| CePLeadingLogMix1BB | `MDC2025au_best_v1_3` | 2 |
| CePLeadingLogOnSpill | `MDC2025au_best_v1_5` | 3 |
| CePlusEndpointOnSpill | `MDC2025an_best_v1_1` | 1 |
| CosmicCRYAllOnSpill | `MDC2025au_best_v1_5` | 2 |
| CosmicCRYExtracted | `MDC2025au_best_v1_5` | 2 |
| CosmicCalibOnSpill | `MDC2025ap_best_v1_1` | 1 |
| CosmicSignalMix1BB | `MDC2025au_best_v1_3` | 2 |
| CosmicSignalOffSpill | `MDC2025an_best_v1_1` | 1 |
| CosmicSignalOnSpill | `MDC2025au_best_v1_5` | 3 |
| DIOtail95Mix1BB | `MDC2025au_best_v1_3` | 2 |
| DIOtail95OnSpill | `MDC2025au_best_v1_5` | 3 |
| FlatGammaCaloMix1BB | `MDC2025au_best_v1_3` | 3 |
| FlatGammaCaloOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatGammaMix1BB | `MDC2025au_best_v1_3` | 3 |
| FlatGammaOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatMuMinusOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlateMinusMix1BB | `MDC2025au_best_v1_3` | 2 |
| FlateMinusOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatePlusMix1BB | `MDC2025au_best_v1_3` | 2 |
| FlatePlusOnSpill | `MDC2025au_best_v1_5` | 2 |
| IPAMuminusMichelMix1BB | `MDC2025au_best_v1_3` | 2 |
| IPAMuminusMichelOnSpill | `MDC2025au_best_v1_5` | 3 |
| MuCap1809keVCaloMix1BB | `MDC2025au_best_v1_3` | 2 |
| MuCap1809keVCaloOnSpill | `MDC2025au_best_v1_5` | 2 |
| NoPrimaryMix1BB | `MDC2025au_best_v1_3` | 2 |
| PBINormal_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PBIPathological_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PbarResamplingMix1BB | `MDC2025au_best_v1_3` | 2 |
| PbarResamplingOnSpill | `MDC2025au_best_v1_5` | 3 |
| RMCExternalOnSpill | `MDC2025an_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025an_best_v1_1` | 1 |
| RMCPhaseSpace0NExternalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RMCPhaseSpace0NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace0NInternalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RMCPhaseSpace0NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NExternalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RMCPhaseSpace1NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NInternalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RMCPhaseSpace1NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RPCExternalOnSpill | `MDC2025au_best_v1_5` | 2 |
| RPCExternalPhysicalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RPCExternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 3 |
| RPCInternalOnSpill | `MDC2025ap_best_v1_1` | 1 |
| RPCInternalPhysicalMix1BB | `MDC2025au_best_v1_3` | 2 |
| RPCInternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 3 |
| ensembleMDS3bOnSpill | `MDC2025au_best_v1_1` | 1 |
| ensembleMDS3cMix1BB | `MDC2025au_best_v1_3` | 3 |
| ensembleMDS3cOnSpill | `MDC2025au_best_v1_5` | 2 |

### Not at MDC2025au (9)

| description | latest dsconf | versions |
|---|---|---:|
| CeEndpointOnSpill | `MDC2025an_best_v1_1` | 1 |
| CePlusEndpointOnSpill | `MDC2025an_best_v1_1` | 1 |
| CosmicCalibOnSpill | `MDC2025ap_best_v1_1` | 1 |
| CosmicSignalOffSpill | `MDC2025an_best_v1_1` | 1 |
| PBINormal_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PBIPathological_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| RMCExternalOnSpill | `MDC2025an_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025an_best_v1_1` | 1 |
| RPCInternalOnSpill | `MDC2025ap_best_v1_1` | 1 |

---

# mcs

```bash
python3 bin/latestDatasets --defname 'mcs.mu2e.%.MDC2025%best%.art' --show-count | grep -v Trig
```

### Campaign spread

| dsconf | datasets |
|---|---:|
| `MDC2025au_best_v1_5` | 22 |
| `MDC2025au_best_v1_1` | 19 |
| `MDC2025an_best_v1_1` | 13 |
| `MDC2025ar_best_v1_1` | 5 |
| `MDC2025ai_best_v1_3` | 2 |
| `MDC2025aq_best_v1_1` | 2 |

### Datasets (48)

| description | dsconf | versions |
|---|---|---:|
| CeMLeadingLogMix1BB | `MDC2025au_best_v1_1` | 2 |
| CeMLeadingLogOnSpill | `MDC2025au_best_v1_5` | 3 |
| CePLeadingLogMix1BB | `MDC2025au_best_v1_1` | 2 |
| CePLeadingLogOnSpill | `MDC2025au_best_v1_5` | 4 |
| CosmicCRYAllOnSpill | `MDC2025au_best_v1_5` | 3 |
| CosmicCRYExtracted | `MDC2025au_best_v1_5` | 3 |
| CosmicCalibOnSpill | `MDC2025ar_best_v1_1` | 2 |
| CosmicSignalMix1BB | `MDC2025au_best_v1_1` | 2 |
| CosmicSignalOffSpill-CH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOffSpill-LH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOnSpill | `MDC2025au_best_v1_5` | 3 |
| DIOtail95Mix1BB | `MDC2025au_best_v1_1` | 2 |
| DIOtail95OnSpill | `MDC2025au_best_v1_5` | 3 |
| FlatGammaCaloMix1BB | `MDC2025au_best_v1_1` | 3 |
| FlatGammaCaloOnSpill | `MDC2025au_best_v1_5` | 3 |
| FlatGammaMix1BB | `MDC2025au_best_v1_1` | 3 |
| FlatGammaOnSpill | `MDC2025au_best_v1_5` | 3 |
| FlatMuMinusOnSpill | `MDC2025au_best_v1_5` | 3 |
| FlateMinusMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlateMinusOnSpill | `MDC2025au_best_v1_5` | 3 |
| FlatePlusMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlatePlusOnSpill | `MDC2025au_best_v1_5` | 3 |
| IPAMuminusMichelMix1BB | `MDC2025au_best_v1_1` | 2 |
| IPAMuminusMichelOnSpill | `MDC2025au_best_v1_5` | 3 |
| MuCap1809keVCaloMix1BB | `MDC2025au_best_v1_1` | 2 |
| MuCap1809keVCaloOnSpill | `MDC2025au_best_v1_5` | 2 |
| NoPrimaryMix1BB | `MDC2025au_best_v1_1` | 2 |
| PBINormal_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PBIPathological_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PbarResamplingMix1BB | `MDC2025au_best_v1_1` | 2 |
| PbarResamplingOnSpill | `MDC2025au_best_v1_5` | 2 |
| RMCExternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCPhaseSpace0NExternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace0NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace0NInternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace0NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NExternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace1NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NInternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace1NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RPCExternalOnSpill | `MDC2025au_best_v1_5` | 3 |
| RPCExternalPhysicalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RPCExternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 3 |
| RPCInternalPhysicalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RPCInternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 3 |
| ensembleMDS3cMix1BB | `MDC2025au_best_v1_1` | 3 |
| ensembleMDS3cOnSpill | `MDC2025au_best_v1_5` | 3 |

### `-reco` suffixed descriptions (15)

The `-reco` suffix is in the DESCRIPTION, not just the cnf name —
output of the generic chained cnfs, a parallel naming line for the
same physics. Separated so they do not read as the latest version
of the unsuffixed dataset.

| description | dsconf | versions |
|---|---|---:|
| CeEndpointOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| CeMLeadingLogOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| CePLeadingLogOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| CePlusEndpointOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| CosmicSignalOffSpill-CH-reco | `MDC2025an_best_v1_1` | 1 |
| CosmicSignalOffSpill-LH-reco | `MDC2025an_best_v1_1` | 1 |
| CosmicSignalOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| DIOtail95OnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| FlatMuMinusOnSpill-reco | `MDC2025aq_best_v1_1` | 1 |
| FlateMinusOnSpill-reco | `MDC2025aq_best_v1_1` | 1 |
| IPAMuminusMichelOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| RMCExternalOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| RMCInternalOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| RPCExternalPhysicalOnSpill-reco | `MDC2025an_best_v1_1` | 1 |
| RPCInternalPhysicalOnSpill-reco | `MDC2025an_best_v1_1` | 1 |

### Not at MDC2025au (7)

| description | latest dsconf | versions |
|---|---|---:|
| CosmicCalibOnSpill | `MDC2025ar_best_v1_1` | 2 |
| CosmicSignalOffSpill-CH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOffSpill-LH | `MDC2025ar_best_v1_1` | 1 |
| PBINormal_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| PBIPathological_33344Mix1BB | `MDC2025ai_best_v1_3` | 1 |
| RMCExternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025ar_best_v1_1` | 1 |

---

# nts

```bash
python3 bin/latestDatasets --defname 'nts.mu2e.%.MDC2025%best%.root' --show-count | grep -v Trig
```

### Campaign spread

| dsconf | datasets |
|---|---:|
| `MDC2025au_best_v1_5` | 21 |
| `MDC2025au_best_v1_1` | 19 |
| `MDC2025ar_best_v1_1` | 6 |

### Datasets (46)

| description | dsconf | versions |
|---|---|---:|
| CeMLeadingLogMix1BB | `MDC2025au_best_v1_1` | 2 |
| CeMLeadingLogOnSpill | `MDC2025au_best_v1_5` | 2 |
| CePLeadingLogMix1BB | `MDC2025au_best_v1_1` | 2 |
| CePLeadingLogOnSpill | `MDC2025au_best_v1_5` | 2 |
| CosmicCRYAllOnSpill | `MDC2025au_best_v1_5` | 2 |
| CosmicCRYExtracted | `MDC2025ar_best_v1_1` | 1 |
| CosmicCalibOnSpill | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalMix1BB | `MDC2025au_best_v1_1` | 2 |
| CosmicSignalOffSpill-CH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOffSpill-LH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOnSpill | `MDC2025au_best_v1_5` | 2 |
| DIOtail95Mix1BB | `MDC2025au_best_v1_1` | 2 |
| DIOtail95OnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatGammaCaloMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlatGammaCaloOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatGammaMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlatGammaOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatMuMinusOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlateMinusMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlateMinusOnSpill | `MDC2025au_best_v1_5` | 2 |
| FlatePlusMix1BB | `MDC2025au_best_v1_1` | 2 |
| FlatePlusOnSpill | `MDC2025au_best_v1_5` | 2 |
| IPAMuminusMichelMix1BB | `MDC2025au_best_v1_1` | 2 |
| IPAMuminusMichelOnSpill | `MDC2025au_best_v1_5` | 2 |
| MuCap1809keVCaloMix1BB | `MDC2025au_best_v1_1` | 2 |
| MuCap1809keVCaloOnSpill | `MDC2025au_best_v1_5` | 2 |
| NoPrimaryMix1BB | `MDC2025au_best_v1_1` | 2 |
| PbarResamplingMix1BB | `MDC2025au_best_v1_1` | 1 |
| PbarResamplingOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCExternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCPhaseSpace0NExternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace0NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace0NInternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace0NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NExternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace1NExternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RMCPhaseSpace1NInternalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RMCPhaseSpace1NInternalOnSpill | `MDC2025au_best_v1_5` | 1 |
| RPCExternalOnSpill | `MDC2025au_best_v1_5` | 2 |
| RPCExternalPhysicalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RPCExternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 2 |
| RPCInternalPhysicalMix1BB | `MDC2025au_best_v1_1` | 2 |
| RPCInternalPhysicalOnSpill | `MDC2025au_best_v1_5` | 2 |
| ensembleMDS3cMix1BB | `MDC2025au_best_v1_1` | 2 |
| ensembleMDS3cOnSpill | `MDC2025au_best_v1_5` | 2 |

### Not at MDC2025au (6)

| description | latest dsconf | versions |
|---|---|---:|
| CosmicCRYExtracted | `MDC2025ar_best_v1_1` | 1 |
| CosmicCalibOnSpill | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOffSpill-CH | `MDC2025ar_best_v1_1` | 1 |
| CosmicSignalOffSpill-LH | `MDC2025ar_best_v1_1` | 1 |
| RMCExternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
| RMCInternalOnSpill | `MDC2025ar_best_v1_1` | 1 |
