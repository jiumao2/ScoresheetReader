# FIBA scoresheet notation audit

Sources:

- [FIBA Official Basketball Rules 2024, Appendix B](https://assets.fiba.basketball/image/upload/documents-corporate-fiba-official-rules-2024-v10a.pdf)
- [FIBA Official Basketball Rules 2026 v1.1, Appendix B](https://assets.fiba.basketball/image/upload/documents-corporate-fiba-official-rules-2026-v1-1.pdf), valid from 1 October 2026

## Active profile: FIBA 2024

| Area | Project behaviour |
| --- | --- |
| Team list closure | If 11 players are listed, draw one horizontal line through the next row up to the player-in column. If fewer than 11 are listed, continue diagonally from the foul-section boundary to the bottom-right of the player foul area. |
| Time-outs | Place the game minute at the geometric centre of its box. Close each unused box with two contained horizontal parallel lines. |
| Team fouls | Draw a large contained `X` for each used box and two contained parallel lines for unused boxes. |
| Player/coach foul cells | Players have 5 formal cells. The head coach and first assistant coach rows each have exactly 3 formal cells. Later rule markers are stored and rendered in the unboxed column after the last formal cell. |
| Coach role changes | Before a role change, a technical foul by the first assistant coach is recorded as `B` against the head coach. If the head coach cannot continue, Art. 7.7 gives the first assistant coach all head-coach duties and powers; the editor therefore permits `C`, `B`, `D`, and `F` in the first assistant coach's own 3-cell row. Fight disqualification examples such as `D2 F F` remain representable without treating them as the only legal sequence. |
| Ordinary foul codes | Player: `P`, `T`, `U`, `D`. Head coach or acting head coach: `C`, `B`, `D`, `F`. `GD`, `D`, and `F` may be represented as post-foul markers where Appendix B requires the following/outside space. |
| Free throws | Render `1`, `2`, or `3` at the lower-right of the foul code. |
| Cancelled penalties | Render a small `c` beside the code on the same baseline (`Pc`). A cancelled mark and a free-throw number are mutually exclusive, so the model cannot create `P2c`. |
| Scoring | Free throw: filled dot. Two points: diagonal slash. Three points: diagonal slash plus a circle around the scorer number. |
| Period/game end | Period end remains an explicit semantic event. Game-end double lines and the remaining-column diagonal are generated automatically for each team once both final scores match the last cumulative events. |
| Ink | Phase 1 uses black only. Logical quarter colour roles remain in the data for a later red/blue profile. |

## Reserved profile: FIBA 2026

The 2026 profile is represented in `shared/rule_profiles.json` but is not selectable in the editor. Its data design already supports:

- `DI` for a disruptive foul;
- `FL` for a flagrant foul;
- category 1 and category 2 technical fouls that both print as `T` but differ by a circle;
- circled `C`, `B`, or `BD` forms that count towards game disqualification;
- a separate plain `BD` post-foul/delegation-disqualification marker;
- a stable catalogue ID independent of the printed letters, preventing ambiguous `T` or `BD` values after a future rules-profile switch.

Activation of `fiba_2026` must add its editor catalogue, validation rules, examples, and migration tests. Existing `fiba_2024` documents retain their stored profile and rendering; they are never silently reinterpreted under the newer rules.

## Template-specific note

The project template is a local 2019 Chinese form rather than FIBA's official blank form. Its exact measured coordinates remain template-specific, while semantic notation and closure rules follow the selected FIBA profile. A future template adapter should therefore map the same semantic document to different measured cell definitions instead of embedding template geometry in the domain model.
