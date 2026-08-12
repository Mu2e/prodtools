# Mu2e Analysis Computing Organization

Baseline: the official 2025-12 chart (`Mu2e_Analysis_Computing_Organization_2025-12_diagram.png`).
The mermaid source below carries corrections applied since that chart was
published, so it and the `.png` no longer agree — the mermaid is the current one.

## Corrections since 2025-12

All from MacKenzie, org chat, 2026-08-12.

**Simulation subgroup:**

- **Geant4 and Geometry are a single subgroup**, and have been almost since the
  beginning. The 2025-12 chart still shows them split (`Geometry: Tripathy`,
  `Geant4: Cao, Tripathy`); they are merged here into one node.
- **Max Jenkinson (U. Manchester)** leads the merged Geant4 & Geometry subgroup.
- **Tausiff Houssain** leads Generators, replacing Borrel.
- Neither subgroup lead currently has experience modifying the Offline geometry,
  so those changes are being made by MacKenzie directly. Treat the chart as
  reporting lines, not as a routing table for geometry work.

**Leadership and spelling:**

- **Trigger is led by MacKenzie alone, with no deputy** — the 2025-12 chart
  lists `Demers, MacKinzie`.
- The surname is **MacKenzie**, not "MacKinzie".
- **Operations deputy is Lei Xia**, replacing Grant.
- ML/AI is **Corrodi, Kampa** — the chart spelled Cole Kampa's surname "Campa"
  in ML/AI while spelling it correctly under Field Map.

```mermaid
%%{init: { 'theme': 'base', 'flowchart': { 'htmlLabels': true, 'nodeSpacing': 20, 'rankSpacing': 30, 'padding': 5, 'useMaxWidth': false, 'curve': 'linear' }, 'securityLevel': 'loose' } }%%
graph TD
    SPK[Spokespersons:<br/>Bernstein, Miscetti&nbsp;]
    
    SPK --> AC[Analysis Coordinators:<br/>Echenard, Oksuzian&nbsp;]
    
    AC --> INFGROUP
    AC --> SIMGROUP
    AC --> RECOGROUP
    AC --> TOOLSGROUP
    AC --> CALGROUP
    AC --> TRIGGROUP
    SPK --> OC[Operations Coordinators:<br/>Rackness, Xia&nbsp;]
    OC --> TRIGGROUP
    AC -.-> ML[ML/AI:<br/>Corrodi, Kampa&nbsp;]
    
    subgraph INFGROUP[" "]
        INF[Infrastructure:<br/>Culbertson]
        PROD[Production:<br/>Oksuzian]
        CODE[Code Management:<br/>Culbertson]
        DATA[Data Handling:<br/>Tran]
        DB[Databases:<br/>Culbertson]
        DQM[Offline DQM:<br/>Tedeschi]
    end
    
    subgraph SIMGROUP[" "]
        SIM[Simulation:<br/>DiFalco]
        GEN[Generators:<br/>Houssain]
        G4GEOM[Geant4 &amp; Geometry:<br/>Jenkinson]
        NONG4[Other MC:<br/>Mueller]
    end
    
    subgraph RECOGROUP[" "]
        RECO[Reconstruction:<br/>Brown]
        ALG[Algorithms:<br/>Brown]
        VAL[Validation:<br/>Culbertson]
    end
    
    subgraph TOOLSGROUP[" "]
        TOOLS[Tools:<br/>TBD, Middleton&nbsp;]
        NTUP[Analysis Tuple:<br/>TBD]
        IFACE[Analysis Interfaces:<br/>Harrison]
        EVD[Event Display:<br/>Chithirasreemadam]
        REF[Reference Analyses:<br/>Middleton]
    end
    
    subgraph CALGROUP[" "]
        CAL[Calibration & Alignment:<br/>Bonventre]
        CALIB[Calibration:<br/>Group]
        ALIGN[Alignment:<br/>Palo]
        FMAP[Field Map:<br/>Kampa]
    end
    
    subgraph TRIGGROUP[" "]
        TRIG[Trigger:<br/>MacKenzie]
    end
    linkStyle 9 stroke:none,stroke-width:0
    
    classDef spokespersons fill:#1E40AF,stroke:#1E3A8A,stroke-width:2px,color:#fff
    classDef analysis fill:#7C3AED,stroke:#6D28D9,stroke-width:2px,color:#fff
    classDef operations fill:#BE185D,stroke:#9F1239,stroke-width:2px,color:#fff
    classDef tools fill:#D97706,stroke:#B45309,stroke-width:2px,color:#fff
    classDef infrastructure fill:#059669,stroke:#047857,stroke-width:2px,color:#fff
    classDef infrastructureLeader fill:#059669,stroke:#065F46,stroke-width:6px,color:#fff
    classDef simulation fill:#0891B2,stroke:#0C5D7A,stroke-width:2px,color:#fff
    classDef simulationLeader fill:#0891B2,stroke:#0C5D7A,stroke-width:6px,color:#fff
    classDef reconstruction fill:#2563EB,stroke:#1E3A8A,stroke-width:2px,color:#fff
    classDef reconstructionLeader fill:#2563EB,stroke:#1E3A8A,stroke-width:6px,color:#fff
    classDef calibration fill:#DC2626,stroke:#991B1B,stroke-width:2px,color:#fff
    classDef calibrationLeader fill:#DC2626,stroke:#991B1B,stroke-width:6px,color:#fff
    classDef toolsLeader fill:#D97706,stroke:#92400E,stroke-width:6px,color:#fff
    classDef operationsLeader fill:#BE185D,stroke:#881337,stroke-width:6px,color:#fff
    classDef triggerLeader fill:#BE185D,stroke:#881337,stroke-width:6px,color:#fff
    classDef mlai fill:#374151,stroke:#1F2937,stroke-width:2px,color:#fff
    
    class SPK spokespersons
    class AC analysis
    class OC operations
    class ML mlai
    class TRIG triggerLeader
    class TOOLS toolsLeader
    class NTUP,IFACE,EVD,REF tools
    class INF infrastructureLeader
    class PROD,CODE,DATA,DB,DQM infrastructure
    class SIM simulationLeader
    class GEN,G4GEOM,NONG4 simulation
    class RECO reconstructionLeader
    class ALG,VAL reconstruction
    class CAL calibrationLeader
    class CALIB,ALIGN,FMAP calibration
```

