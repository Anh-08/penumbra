export function connectFleetSocket(url, onMessage, onDisconnect, options = {}) {
  const Socket = options.Socket ?? WebSocket;
  const schedule = options.schedule ?? window.setTimeout.bind(window);
  const cancel = options.cancel ?? window.clearTimeout.bind(window);
  let socket;
  let retryTimer;
  let stopped = false;
  let attempts = 0;

  function connect() {
    if (stopped) return;
    try {
      socket = new Socket(url);
      socket.onmessage = (event) => {
        attempts = 0;
        onMessage(event);
      };
      socket.onclose = () => {
        if (stopped) return;
        onDisconnect();
        retryTimer = schedule(connect, Math.min(1000 * 2 ** attempts++, 8000));
      };
      socket.onerror = () => socket.close();
    } catch {
      onDisconnect();
      retryTimer = schedule(connect, Math.min(1000 * 2 ** attempts++, 8000));
    }
  }

  connect();
  return () => {
    stopped = true;
    if (retryTimer != null) cancel(retryTimer);
    socket?.close();
  };
}
