# IFC QA report — sample_house.ifc

Schema **IFC4** · 28 elements · 2 storeys · 4 spaces · length unit METRE

## At a glance

- Clashes: **3 hard**, **1 duplicate**, **1 clearance** (< 50 mm)
- Authored quantities that disagree with geometry: **1** of 14 checked
- Natural ventilation (HK Building (Planning) Regulations 30/31 prescriptive route): **2 pass / 2 fail**

## Clashes

| # | kind | element A | element B | depth / gap (mm) | location (x, y, z) |
|---|---|---|---|---|---|
| 1 | duplicate | Wall 1F-INT (WALL) | Wall 1F-INT (copy) (WALL) | 100 | 5.05, 4.00, 4.70 |
| 2 | hard | Duct SA-01 400x300 (DUCTSEGMENT) | Wall GF-E (WALL) | 200 | 9.90, 2.00, 2.75 |
| 3 | hard | Beam B1 300x500 (BEAM) | Wall GF-INT (WALL) | 100 | 6.05, 4.00, 2.75 |
| 4 | hard | Door 1F-INT (DOOR) | Wall 1F-INT (copy) (WALL) | 100 | 5.05, 1.65, 4.25 |
| 5 | clearance | Beam B1 300x500 (BEAM) | Pipe CW-01 DN100 (PIPESEGMENT) | 30 | 3.00, 4.00, 2.48 |

## Natural ventilation

| space | floor m² | facades | glazing m² (req.) | openable m² (req.) | depth m (limit) | ACH* | result |
|---|---|---|---|---|---|---|---|
| Living | 44.08 | S+W (corner) | 7.2 (4.408) | 3.6 (2.755) | 5.8 (7.5) | 7.67 | PASS |
| Kitchen | 28.12 | N (single-sided) | 1.44 (2.812) | 0.72 (1.758) | 7.6 (7.5) | 2.3 | FAIL |
| Bedroom 1 | 36.48 | S+W (corner) | 4.2 (3.648) | 0.36 (2.28) | 4.8 (7.5) | 0.89 | FAIL |
| Bedroom 2 | 35.72 | E+S (corner) | 4.5 (3.572) | 2.25 (2.232) | 4.7 (7.5) | 5.92 | PASS |

*Indicative air changes per hour, BS 5925 single-sided, U=3.0 m/s, dT=3.0 K. Screening aid for design review, not a code-compliance certificate.

## Quantity take-off (by IFC class)

| class | count | length m | area m² (walls: net side) | gross vol m³ | net vol m³ |
|---|---|---|---|---|---|
| BEAM | 1 | 9.6 |  | 1.44 |  |
| DOOR | 3 |  | 5.88 | 0.588 |  |
| DUCTSEGMENT | 1 | 4.9 |  | 0.588 |  |
| PIPESEGMENT | 1 | 7.4 |  | 0.058 |  |
| SLAB | 3 |  | 240.0 | 48.0 | 48.0 |
| WALL | 11 | 93.2 | 256.38 | 49.08 | 44.814 |
| WINDOW | 8 |  | 17.34 | 1.734 |  |

## Authored quantities vs geometry

| element | quantity | authored | computed | diff % |
|---|---|---|---|---|
| Wall GF-N | Qto_WallBaseQuantities.NetVolume | 6.6 | 5.712 | +15.5 |

## Plans

![plan_ground_floor.png](plan_ground_floor.png)
![plan_first_floor.png](plan_first_floor.png)
