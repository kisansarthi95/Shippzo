// metro.config.js
const { getDefaultConfig } = require("expo/metro-config");
const path = require('path');
const { FileStore } = require('metro-cache');

const config = getDefaultConfig(__dirname);

// Use a stable on-disk store (shared across web/android)
const root = process.env.METRO_CACHE_ROOT || path.join(__dirname, '.metro-cache');
config.cacheStores = [
  new FileStore({ root: path.join(root, 'cache') }),
];

// Fix: Android Expo Go requests scoped package assets without the
// 'node_modules/' prefix (e.g. ?unstable_path=@expo/vector-icons/...)
// Metro can't resolve these — prepend 'node_modules/' so Metro finds them.
// const originalMiddleware = config.server?.enhanceMiddleware;
// config.server = {
//   ...config.server,
//   enhanceMiddleware: (middleware, server) => {
//     const enhanced = originalMiddleware
//       ? originalMiddleware(middleware, server)
//       : middleware;
//     return (req, res, next) => {
//       if (req.url && req.url.includes('/assets/') && req.url.includes('unstable_path=')) {
//         const url = new URL(req.url, 'http://localhost');
//         const unstablePath = url.searchParams.get('unstable_path');
//         if (unstablePath && unstablePath.startsWith('@') && !unstablePath.startsWith('node_modules/')) {
//           url.searchParams.set('unstable_path', 'node_modules/' + unstablePath);
//           req.url = url.pathname + '?' + url.searchParams.toString();
//         }
//       }
//       return enhanced(req, res, next);
//     };
//   },
// };

// Reduce the number of workers to decrease resource usage
config.maxWorkers = 1;

module.exports = config;
