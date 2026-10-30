# api

Thin HTTP route handlers only (`/api/v1`). Handlers parse input, call domain services, and map results to responses. No business logic or direct filesystem access here. Every endpoint must go through the central `policy` module. Not implemented yet (M00 placeholder).
