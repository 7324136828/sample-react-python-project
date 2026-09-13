import { useCallback, useEffect, useState } from 'react';

interface Counter {
  count: number;
  updated_at: string;
}

type PendingAction = '' | 'loading' | 'click' | 'clear';

async function counterRequest(path: string, options?: RequestInit): Promise<Counter> {
  const response = await fetch(path, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }
  return (await response.json()) as Counter;
}

export default function App() {
  const [count, setCount] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction>('loading');
  const [error, setError] = useState('');

  const applyCounter = useCallback((counter: Counter) => {
    setCount(counter.count);
    setUpdatedAt(counter.updated_at);
    setError('');
  }, []);

  useEffect(() => {
    let cancelled = false;
    counterRequest('/api/counter')
      .then((counter) => {
        if (!cancelled) applyCounter(counter);
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not reach the backend. Make sure the run script is active.');
        }
      })
      .finally(() => {
        if (!cancelled) setPendingAction('');
      });
    return () => {
      cancelled = true;
    };
  }, [applyCounter]);

  const recordClick = useCallback(async (): Promise<Counter> => {
    setPendingAction('click');
    try {
      const counter = await counterRequest('/api/counter/click', { method: 'POST' });
      applyCounter(counter);
      return counter;
    } catch (requestError) {
      setError('The click was not saved. Please try again.');
      throw requestError;
    } finally {
      setPendingAction('');
    }
  }, [applyCounter]);

  const clearCount = useCallback(async (): Promise<Counter> => {
    setPendingAction('clear');
    try {
      const counter = await counterRequest('/api/counter', { method: 'DELETE' });
      applyCounter(counter);
      return counter;
    } catch (requestError) {
      setError('The count could not be cleared. Please try again.');
      throw requestError;
    } finally {
      setPendingAction('');
    }
  }, [applyCounter]);

  useEffect(() => {
    const context = document.modelContext;
    if (!context?.registerTool) return undefined;

    const lifecycle = new AbortController();
    const emptyInput: ModelContextTool['inputSchema'] = {
      type: 'object',
      properties: {},
      additionalProperties: false,
    };
    try {
      const registrations = [
        context.registerTool(
          {
            name: 'record_click',
            title: 'Record click',
            description: 'Increment the persistent server-backed click count once.',
            inputSchema: emptyInput,
            annotations: { readOnlyHint: false, untrustedContentHint: false },
            async execute() {
              const counter = await recordClick();
              return { count: counter.count, updatedAt: counter.updated_at };
            },
          },
          { signal: lifecycle.signal },
        ),
        context.registerTool(
          {
            name: 'clear_click_count',
            title: 'Clear click count',
            description: 'Reset the persistent server-backed click count to zero.',
            inputSchema: emptyInput,
            annotations: { readOnlyHint: false, untrustedContentHint: false },
            async execute() {
              const counter = await clearCount();
              return { count: counter.count, updatedAt: counter.updated_at };
            },
          },
          { signal: lifecycle.signal },
        ),
      ];
      void Promise.all(registrations).catch(() => {
        // WebMCP is optional; visible controls remain fully functional.
      });
    } catch {
      lifecycle.abort();
      return undefined;
    }
    return () => lifecycle.abort();
  }, [clearCount, recordClick]);

  const isBusy = Boolean(pendingAction);
  const lastUpdated = updatedAt
    ? new Intl.DateTimeFormat(undefined, {
        dateStyle: 'medium',
        timeStyle: 'medium',
      }).format(new Date(updatedAt))
    : 'Waiting for the backend';

  return (
    <main className="page-shell">
      <section className="counter-card" aria-labelledby="page-title">
        <div className="eyebrow">
          <span className="status-dot" aria-hidden="true" />
          Stored by Python
        </div>

        <header>
          <h1 id="page-title">Persistent click counter</h1>
          <p>Every click is written to SQLite and remains after the app closes.</p>
        </header>

        <div className="count-panel" aria-live="polite" aria-busy={isBusy}>
          <span className="count-label">Total clicks</span>
          <strong className="count-value">
            {count === null ? <span className="loading-value">&mdash;</span> : count}
          </strong>
          <span className="updated-time">Last saved: {lastUpdated}</span>
        </div>

        <button
          className="click-button"
          type="button"
          onClick={recordClick}
          disabled={isBusy}
        >
          {pendingAction === 'click' ? 'Saving...' : 'Record a click'}
        </button>

        <button
          className="clear-button"
          type="button"
          onClick={clearCount}
          disabled={isBusy || !count}
        >
          {pendingAction === 'clear' ? 'Clearing...' : 'Clear count'}
        </button>

        <p className={`message ${error ? 'message-error' : ''}`} role="status">
          {error || 'The backend is the source of truth for this number.'}
        </p>
      </section>
    </main>
  );
}
