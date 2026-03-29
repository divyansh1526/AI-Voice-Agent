/**
 * GeminiClient: Handles WebSocket communication with the backend.
 * Supports custom wsUrl (per-agent) and JWT token auth.
 */
class GeminiClient {
  constructor(config) {
    this.wsUrl   = config.wsUrl || null; // explicit WS URL (agent mode)
    this.onOpen  = config.onOpen;
    this.onMessage = config.onMessage;
    this.onClose = config.onClose;
    this.onError = config.onError;
    this.websocket = null;
  }

  connect() {
    // Use provided wsUrl or auto-derive from current location
    const url = this.wsUrl || (() => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${protocol}//${window.location.host}/ws`;
    })();

    this.websocket = new WebSocket(url);
    this.websocket.binaryType = 'arraybuffer';

    this.websocket.onopen    = () => { if (this.onOpen)    this.onOpen(); };
    this.websocket.onmessage = (e) => { if (this.onMessage) this.onMessage(e); };
    this.websocket.onclose   = (e) => { if (this.onClose)  this.onClose(e); };
    this.websocket.onerror   = (e) => { if (this.onError)  this.onError(e); };
  }

  send(data) {
    if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
      this.websocket.send(data);
    }
  }

  sendText(text) {
    this.send(text); // backend accepts raw text on the text_input_queue
  }

  sendJson(obj) {
    this.send(JSON.stringify(obj));
  }

  sendImage(base64Data, mimeType = 'image/jpeg') {
    this.sendJson({ type: 'image', mime_type: mimeType, data: base64Data });
  }

  disconnect() {
    if (this.websocket) {
      this.websocket.close();
      this.websocket = null;
    }
  }

  isConnected() {
    return this.websocket && this.websocket.readyState === WebSocket.OPEN;
  }
}
