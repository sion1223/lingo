import { cloneStroke } from "./attempt-log.js";

export const LOCAL_ATTEMPT_DATABASE = "lingo-writing-v1";
export const LOCAL_ATTEMPT_STORE = "attempts";
export const LOCAL_ATTEMPT_SCHEMA_VERSION = 1;

function cloneStrokeResult(entry) {
  return {
    ...entry,
    stroke: entry.stroke ? cloneStroke(entry.stroke) : null,
  };
}

export function buildLocalAttemptRecord({
  sessionId,
  attemptId,
  attemptRevision,
  character,
  mode,
  startedAt,
  savedAt,
  strokes,
  strokeResults,
  endedReason = null,
  endedAt = null,
  finalScore = null,
}) {
  const updatedAt = savedAt || new Date().toISOString();
  const finished = Boolean(endedReason);
  return {
    schema_version: LOCAL_ATTEMPT_SCHEMA_VERSION,
    attempt_id: attemptId,
    session_id: sessionId,
    attempt_revision: attemptRevision,
    char: character,
    mode,
    status: finished ? "finished" : "active",
    ended_reason: endedReason,
    started_at: startedAt,
    updated_at: updatedAt,
    ended_at: finished ? (endedAt || updatedAt) : null,
    strokes: strokes.map(cloneStroke),
    stroke_results: strokeResults.map(cloneStrokeResult),
    final_score: Number.isFinite(finalScore) ? finalScore : null,
  };
}

function requestResult(request) {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error || new Error("IndexedDB request failed"));
  });
}

function transactionComplete(transaction) {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(
      transaction.error || new Error("IndexedDB transaction aborted"),
    );
    transaction.onerror = () => reject(
      transaction.error || new Error("IndexedDB transaction failed"),
    );
  });
}

export class LocalAttemptStore {
  constructor({
    indexedDB = globalThis.indexedDB,
    databaseName = LOCAL_ATTEMPT_DATABASE,
  } = {}) {
    this.indexedDB = indexedDB;
    this.databaseName = databaseName;
    this.databasePromise = null;
    this.writeTail = Promise.resolve();
  }

  get available() {
    return Boolean(this.indexedDB?.open);
  }

  open() {
    if (!this.available) {
      return Promise.reject(new Error("IndexedDB is unavailable"));
    }
    if (this.databasePromise) return this.databasePromise;
    this.databasePromise = new Promise((resolve, reject) => {
      const request = this.indexedDB.open(this.databaseName, 1);
      request.onupgradeneeded = () => {
        const database = request.result;
        if (database.objectStoreNames.contains(LOCAL_ATTEMPT_STORE)) return;
        const store = database.createObjectStore(LOCAL_ATTEMPT_STORE, {
          keyPath: "attempt_id",
        });
        store.createIndex("updated_at", "updated_at");
        store.createIndex("char", "char");
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => {
        this.databasePromise = null;
        reject(request.error || new Error("Could not open the local writing database"));
      };
      request.onblocked = () => {
        this.databasePromise = null;
        reject(new Error("Local writing database upgrade was blocked"));
      };
    });
    return this.databasePromise;
  }

  save(record) {
    const operation = this.writeTail
      .catch(() => undefined)
      .then(async () => {
        const database = await this.open();
        const transaction = database.transaction(LOCAL_ATTEMPT_STORE, "readwrite");
        transaction.objectStore(LOCAL_ATTEMPT_STORE).put(record);
        await transactionComplete(transaction);
        return record;
      });
    this.writeTail = operation;
    return operation;
  }

  async count() {
    await this.writeTail.catch(() => undefined);
    const database = await this.open();
    const transaction = database.transaction(LOCAL_ATTEMPT_STORE, "readonly");
    return requestResult(transaction.objectStore(LOCAL_ATTEMPT_STORE).count());
  }

  async listRecent(limit = 8) {
    await this.writeTail.catch(() => undefined);
    const database = await this.open();
    const transaction = database.transaction(LOCAL_ATTEMPT_STORE, "readonly");
    const index = transaction.objectStore(LOCAL_ATTEMPT_STORE).index("updated_at");
    return new Promise((resolve, reject) => {
      const records = [];
      const request = index.openCursor(null, "prev");
      request.onsuccess = () => {
        const cursor = request.result;
        if (!cursor || records.length >= Math.max(0, limit)) {
          resolve(records);
          return;
        }
        records.push(cursor.value);
        cursor.continue();
      };
      request.onerror = () => reject(
        request.error || new Error("Could not read local writing history"),
      );
      transaction.onabort = () => reject(
        transaction.error || new Error("Local writing history read was aborted"),
      );
    });
  }
}
