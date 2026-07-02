/**
 * PersistentStorage - Wrapper around localStorage with type safety and namespace
 * 
 * Provides:
 * - Type-safe get/set
 * - Automatic JSON parsing
 * - Storage event listening
 * - Default values
 * - Namespace isolation
 */

const STORAGE_NAMESPACE = 'sentinel-flare';

/**
 * @template T
 * @typedef {Object} StorageOptions
 * @property {T} defaultValue - Default if not found
 * @property {boolean} useSession - Use sessionStorage instead of localStorage
 */

/**
 * @template T
 * @typedef {Object} StoredData
 * @property {T} value - The stored value
 * @property {string} timestamp - ISO timestamp
 * @property {number} version - Schema version
 */

class PersistentStorage {
  constructor(namespace = STORAGE_NAMESPACE) {
    this.namespace = namespace;
    this.storage = localStorage;
    this.sessionStorage = sessionStorage;
    
    // Bind storage event listener
    this.handleStorageEvent = this.handleStorageEvent.bind(this);
    if (typeof window !== 'undefined') {
      window.addEventListener('storage', this.handleStorageEvent);
    }
  }

  /**
   * Get the full key with namespace
   * @param {string} key 
   * @returns {string}
   */
  getKey(key) {
    return `${this.namespace}:${key}`;
  }

  /**
   * Get storage backend
   * @param {boolean} useSession 
   * @returns {Storage}
   */
  getBackend(useSession = false) {
    return useSession ? this.sessionStorage : this.storage;
  }

  /**
   * Get a value from storage
   * @template T
   * @param {string} key - Storage key
   * @param {StorageOptions<T>} options - Options
   * @returns {T|null}
   */
  get(key, { defaultValue = null, useSession = false } = {}) {
    try {
      const backend = this.getBackend(useSession);
      const raw = backend.getItem(this.getKey(key));
      
      if (!raw) return defaultValue;
      
      const data = JSON.parse(raw);
      return data.value !== undefined ? data.value : defaultValue;
    } catch (err) {
      console.warn(`Storage get error for ${key}:`, err);
      return defaultValue;
    }
  }

  /**
   * Set a value in storage
   * @template T
   * @param {string} key - Storage key
   * @param {T} value - Value to store
   * @param {boolean} useSession - Use sessionStorage
   * @returns {boolean}
   */
  set(key, value, useSession = false) {
    try {
      const backend = this.getBackend(useSession);
      const data = {
        value,
        timestamp: new Date().toISOString(),
        version: 1,
      };
      
      backend.setItem(this.getKey(key), JSON.stringify(data));
      return true;
    } catch (err) {
      console.error(`Storage set error for ${key}:`, err);
      return false;
    }
  }

  /**
   * Remove a value from storage
   * @param {string} key - Storage key
   * @param {boolean} useSession - Use sessionStorage
   * @returns {boolean}
   */
  remove(key, useSession = false) {
    try {
      const backend = this.getBackend(useSession);
      backend.removeItem(this.getKey(key));
      return true;
    } catch (err) {
      console.warn(`Storage remove error for ${key}:`, err);
      return false;
    }
  }

  /**
   * Clear all values in namespace
   * @param {boolean} useSession - Use sessionStorage
   */
  clear(useSession = false) {
    try {
      const backend = this.getBackend(useSession);
      const keys = [];
      
      for (let i = 0; i < backend.length; i++) {
        const key = backend.key(i);
        if (key && key.startsWith(`${this.namespace}:`)) {
          keys.push(key);
        }
      }
      
      keys.forEach(key => backend.removeItem(key));
    } catch (err) {
      console.warn('Storage clear error:', err);
    }
  }

  /**
   * Get all keys in namespace
   * @param {boolean} useSession - Use sessionStorage
   * @returns {string[]}
   */
  keys(useSession = false) {
    try {
      const backend = this.getBackend(useSession);
      const result = [];
      
      for (let i = 0; i < backend.length; i++) {
        const key = backend.key(i);
        if (key && key.startsWith(`${this.namespace}:`)) {
          result.push(key.replace(`${this.namespace}:`, ''));
        }
      }
      
      return result;
    } catch (err) {
      return [];
    }
  }

  /**
   * Check if key exists
   * @param {string} key - Storage key
   * @param {boolean} useSession - Use sessionStorage
   * @returns {boolean}
   */
  has(key, useSession = false) {
    try {
      return this.getBackend(useSession).hasItem(this.getKey(key));
    } catch (err) {
      return false;
    }
  }

  /**
   * Get storage usage
   * @returns {Object} Usage info
   */
  getUsage() {
    let used = 0;
    let total = 0;
    
    try {
      const keys = this.keys();
      keys.forEach(key => {
        const value = this.get(key);
        if (value) {
          used += JSON.stringify(value).length;
        }
      });
      
      // Estimate available (5MB limit)
      total = 5 * 1024 * 1024;
    } catch (err) {
      console.warn('Storage usage error:', err);
    }
    
    return {
      used,
      available: total - used,
      total,
      percentUsed: (used / total * 100).toFixed(2),
    };
  }

  /**
   * Handle storage events from other tabs
   * @param {StorageEvent} event 
   */
  handleStorageEvent(event) {
    if (!event.key || !event.key.startsWith(this.namespace)) return;
    
    const key = event.key.replace(`${this.namespace}:`, '');
    
    // Dispatch custom event for React components
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('storage:change', {
        detail: {
          key,
          oldValue: event.oldValue ? JSON.parse(event.oldValue) : null,
          newValue: event.newValue ? JSON.parse(event.newValue) : null,
        },
      }));
    }
  }

  /**
   * Subscribe to storage changes
   * @param {Function} callback 
   * @returns {Function} Unsubscribe
   */
  subscribe(callback) {
    if (typeof window !== 'undefined') {
      window.addEventListener('storage:change', (e) => callback(e.detail));
      return () => window.removeEventListener('storage:change', callback);
    }
    return () => {};
  }
}

// Singleton instance
const storage = new PersistentStorage();

export default storage;
export { PersistentStorage };
