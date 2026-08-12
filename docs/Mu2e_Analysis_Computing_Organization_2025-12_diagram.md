# Mu2e Analysis Computing Organization

![Mu2e Analysis Computing organization chart](Mu2e_Analysis_Computing_Organization_2025-12_diagram.png)

Baseline: the official 2025-12 chart. The corrections below have been applied
on top of it, and the PNG above is rendered from the mermaid source at the
bottom of this file — the two are kept in sync by regenerating, never by
editing the image.

**Why the PNG and not a live mermaid block:** GitHub renders mermaid in its own
sandbox with `htmlLabels` and `securityLevel: loose` disabled, so the `<b>` and
`<br/>` markup these labels depend on comes out wrong there. The committed PNG
renders identically everywhere.

**Regenerating** — after editing the mermaid source, re-render and commit both:

````bash
DOC=docs/Mu2e_Analysis_Computing_Organization_2025-12_diagram

# pull the mermaid block out of this file
awk '/^```mermaid/{f=1;next} /^```$/{f=0} f' "$DOC.md" > /tmp/org.mmd

# note: unquoted heredoc, so $HOME expands
cat > /tmp/pptr.json <<JSON
{
  "executablePath": "$HOME/.cache/puppeteer/chrome/linux-131.0.6778.204/chrome-linux64/chrome",
  "args": ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
}
JSON

mmdc -i /tmp/org.mmd -p /tmp/pptr.json -b white -s 2 -o "$DOC.png"
````

`mmdc` on mu2egpvm cannot resolve its own puppeteer cache even after
`npx @puppeteer/browsers install chrome@131.0.6778.204`, hence the explicit
`executablePath`. `--no-sandbox` is required; the gpvm has no user namespaces.
`-w` sets the viewport, not the output resolution — `-s` is the scale knob.

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

<details>
<summary>Mermaid source (edit here, then regenerate the PNG above)</summary>

```mermaid
%%{init: { 'theme': 'base', 'flowchart': { 'htmlLabels': true, 'nodeSpacing': 20, 'rankSpacing': 30, 'padding': 5, 'useMaxWidth': false, 'curve': 'linear' }, 'securityLevel': 'loose' } }%%
graph TD
    SPK[<b>Spokespersons</b><br/>Bernstein, Miscetti&nbsp;]
    
    SPK --> AC[<b>Analysis Coordinators</b><br/>Echenard, Oksuzian&nbsp;]
    
    AC --> INFGROUP
    AC --> SIMGROUP
    AC --> RECOGROUP
    AC --> TOOLSGROUP
    AC --> CALGROUP
    AC --> TRIGGROUP
    SPK --> OC[<b>Operations Coordinators</b><br/>Rackness, Xia&nbsp;]
    OC --> TRIGGROUP
    AC -.-> ML[<b>ML/AI</b><br/>Corrodi, Kampa&nbsp;]
    
    subgraph INFGROUP[" "]
        INF[<b>Infrastructure</b><br/>Culbertson]
        PROD[<b>Production</b><br/>Oksuzian]
        CODE[<b>Code Management</b><br/>Culbertson]
        DATA[<b>Data Handling</b><br/>Tran]
        DB[<b>Databases</b><br/>Culbertson]
        DQM[<b>Offline DQM</b><br/>Tedeschi]
    end
    
    subgraph SIMGROUP[" "]
        SIM[<b>Simulation</b><br/>DiFalco]
        GEN[<b>Generators</b><br/>Houssain]
        G4GEOM[<b>Geant4 & Geometry</b><br/>Jenkinson]
        NONG4[<b>Other MC</b><br/>Mueller]
    end
    
    subgraph RECOGROUP[" "]
        RECO[<b>Reconstruction</b><br/>Brown]
        ALG[<b>Algorithms</b><br/>Brown]
        VAL[<b>Validation</b><br/>Culbertson]
    end
    
    subgraph TOOLSGROUP[" "]
        TOOLS[<b>Tools</b><br/>TBD, Middleton&nbsp;]
        NTUP[<b>Analysis Tuple</b><br/>TBD]
        IFACE[<b>Analysis Interfaces</b><br/>Harrison]
        EVD[<b>Event Display</b><br/>Chithirasreemadam]
        REF[<b>Reference Analyses</b><br/>Middleton]
    end
    
    subgraph CALGROUP[" "]
        CAL[<b>Calibration & Alignment</b><br/>Bonventre]
        CALIB[<b>Calibration</b><br/>Group]
        ALIGN[<b>Alignment</b><br/>Palo]
        FMAP[<b>Field Map</b><br/>Kampa]
    end
    
    subgraph TRIGGROUP[" "]
        TRIG[<b>Trigger</b><br/>MacKenzie]
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

</details>
