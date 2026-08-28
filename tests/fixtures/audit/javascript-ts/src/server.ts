import express from 'express';
const app = express();
app.get('/api/items', (_q, r) => r.json([]));
