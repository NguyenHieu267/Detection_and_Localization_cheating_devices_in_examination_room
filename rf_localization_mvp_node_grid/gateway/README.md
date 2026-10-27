# RF Localization MVP Gateway

This version keeps the original WCL localization flow and adds a configurable room/grid UI.

## Room and grid input

The dashboard asks for:

- Room width (m)
- Room length (m)
- Grid cell width (m)
- Grid cell length (m)

The Gateway automatically calculates the number of columns and rows with:

```text
columns = ceil(room_width / grid_cell_width)
rows    = ceil(room_length / grid_cell_length)
```

If the requested cell size does not divide the room exactly, the final column and/or row is smaller so the grid still covers the complete room. Example:

```text
Room: 10 m × 15 m
Cell: 3 m × 4 m

Columns = ceil(10/3) = 4
Rows    = ceil(15/4) = 4

Actual final column width = 1 m
Actual final row length   = 3 m
```

## API

`GET /api/v1/room` returns the room, derived grid, zone rectangles, and four node positions.

`POST /api/v1/room` accepts:

```json
{
  "width_m": 10,
  "length_m": 15,
  "grid_cell_width_m": 3,
  "grid_cell_length_m": 4
}
```

`GET /api/v1/nodes` returns live ESP01–ESP04 online/offline state.
