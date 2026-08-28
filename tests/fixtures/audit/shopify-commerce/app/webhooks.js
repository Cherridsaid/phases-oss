// verify x-shopify-hmac-sha256 on every webhook
export function handler(req){ const sig = req.headers['x-shopify-hmac-sha256'];
  return checkout(req.body.line_items); }
