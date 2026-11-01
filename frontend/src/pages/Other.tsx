import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { ApiError, api, setToken } from "../api/client";

export function LoginPage({ demo, onDone }: { demo: boolean; onDone: () => void }) {
  const [value, setValue] = useState(demo ? "demo-admin" : "");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setToken(value.trim());
    try {
      await api.whoami();
      onDone();
    } catch (failure) {
      setToken(null);
      setError(failure instanceof ApiError && failure.status === 401 ? "That token was not accepted. Check it and try again." : String(failure));
    }
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>Sign in to review fixes</h1>
      <label className="field">
        Reviewer token
        <input value={value} onChange={(event) => setValue(event.target.value)} autoComplete="off" />
      </label>
      {demo && <p className="hint">This demo accepts the filled-in token. A real deployment refuses it.</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button className="primary" type="submit" disabled={!value.trim()}>
        Sign in
      </button>
    </form>
  );
}

export function ProductsPage() {
  const client = useQueryClient();
  const { data, error, isLoading } = useQuery({ queryKey: ["products"], queryFn: api.products });
  const edit = useMutation({
    mutationFn: (id: number) => api.simulateEdit(id),
    onSuccess: () => client.invalidateQueries(),
  });
  const { data: meta } = useQuery({ queryKey: ["meta"], queryFn: api.meta });

  if (isLoading) return <p className="hint">Loading products.</p>;
  if (error) {
    return (
      <p className="error" role="alert">
        Could not load products: {error.message}
      </p>
    );
  }
  return (
    <>
      <h1>Products</h1>
      {edit.isError && (
        <p className="error" role="alert">
          Could not simulate the edit: {edit.error.message}
        </p>
      )}
      {edit.isSuccess && <p className="hint">Edited in the store. Any open fix for that product is now out of date.</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Product</th>
              <th scope="col">Type</th>
              <th scope="col">Open findings</th>
              {meta?.demo_mode && <th scope="col">Demo</th>}
            </tr>
          </thead>
          <tbody>
            {data?.map((product) => (
              <tr key={product.id}>
                <td className="catalog-voice">{product.title}</td>
                <td>{product.product_type}</td>
                <td>{product.open_rules.length ? product.open_rules.join(", ") : "None"}</td>
                {meta?.demo_mode && (
                  <td>
                    <button onClick={() => edit.mutate(product.id)} disabled={edit.isPending}>
                      Edit as merchant
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function ActivityPage() {
  const { data, error, isLoading } = useQuery({ queryKey: ["audit"], queryFn: api.auditLog, refetchInterval: 5000 });
  if (isLoading) return <p className="hint">Loading activity.</p>;
  if (error) {
    return (
      <p className="error" role="alert">
        Could not load activity: {error.message}
      </p>
    );
  }
  return (
    <>
      <h1>Activity</h1>
      <p className="hint">Every status change and decision. This log cannot be edited, even by the application.</p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Who</th>
              <th scope="col">What</th>
              <th scope="col">On</th>
            </tr>
          </thead>
          <tbody>
            {data?.map((entry) => (
              <tr key={entry.id}>
                <td>{new Date(entry.at).toLocaleString()}</td>
                <td>{entry.actor}</td>
                <td>{entry.action}</td>
                <td>{entry.entity_id.slice(0, 8)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
