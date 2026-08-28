# row level security by tenant_id
def query(tenant_id, sql):
    return f"select * from t where tenant_id = '{tenant_id}'"
