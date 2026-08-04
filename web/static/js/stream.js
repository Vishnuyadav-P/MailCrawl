/* ==========================================================================
   SSE consumption.

   EventSource handles reconnection and sends Last-Event-ID for us. The server
   answers with either the exact missed deltas or a fresh snapshot, so the two
   handlers below are all that is needed: `snapshot` REPLACES state, everything
   else ADDS to it. Because rows are keyed by address, a duplicated event after
   a reconnect is a no-op.
   ========================================================================== */

const Stream = (() => {

  let source = null;

  function close() {
    if (source) {
      source.close();
      source = null;
    }
  }

  function connect(scanId, handlers) {
    close();
    source = new EventSource(`/api/scans/${encodeURIComponent(scanId)}/stream`);

    source.addEventListener('snapshot', (e) => handlers.onSnapshot(JSON.parse(e.data)));
    source.addEventListener('progress', (e) => handlers.onProgress(JSON.parse(e.data)));
    source.addEventListener('emails',   (e) => handlers.onEmails(JSON.parse(e.data)));
    source.addEventListener('phase',    (e) => handlers.onPhase(JSON.parse(e.data)));
    source.addEventListener('stats',    (e) => handlers.onStats(JSON.parse(e.data)));

    source.addEventListener('done', (e) => {
      handlers.onTerminal('completed', JSON.parse(e.data));
      close();
    });

    source.addEventListener('failed', (e) => {
      const data = JSON.parse(e.data);
      handlers.onTerminal(data.status || 'failed', data);
      close();
    });

    source.addEventListener('cancelled', (e) => {
      handlers.onTerminal('cancelled', JSON.parse(e.data));
      close();
    });

    source.onerror = () => {
      // EventSource reconnects on its own while the stream is open. A closed
      // readyState means the server finished and hung up, which is not an error.
      if (source && source.readyState === EventSource.CLOSED) {
        handlers.onDisconnect();
      }
    };

    return source;
  }

  return { connect, close };
})();
