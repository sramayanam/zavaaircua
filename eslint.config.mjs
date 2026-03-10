import js from '@eslint/js';
import html from 'eslint-plugin-html';

const browserGlobals = {
  window: 'readonly', document: 'readonly', console: 'readonly',
  fetch: 'readonly', WebSocket: 'readonly', location: 'readonly',
  setTimeout: 'readonly', clearTimeout: 'readonly',
  setInterval: 'readonly', clearInterval: 'readonly',
  alert: 'readonly', confirm: 'readonly',
  URLSearchParams: 'readonly', URL: 'readonly',
  Event: 'readonly', CustomEvent: 'readonly',
  MutationObserver: 'readonly', IntersectionObserver: 'readonly',
  Date: 'readonly', JSON: 'readonly', Promise: 'readonly',
  parseInt: 'readonly', parseFloat: 'readonly', isNaN: 'readonly',
  Array: 'readonly', Object: 'readonly', String: 'readonly',
  Math: 'readonly', Set: 'readonly', Map: 'readonly',
};

export default [
  { ignores: ['cua/**/*.py', 'cua/**/__pycache__/**', 'infra/**', 'samples/**', 'sql/**'] },
  js.configs.recommended,
  {
    files: ['server.js', 'db.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'commonjs',
      globals: {
        require: 'readonly',
        module: 'readonly',
        exports: 'readonly',
        __dirname: 'readonly',
        __filename: 'readonly',
        process: 'readonly',
        console: 'readonly',
      },
    },
    rules: {
      'no-unused-vars': 'warn',
      'no-console': 'off',
      'eqeqeq': 'error',
      'no-eval': 'error',
    },
  },
  {
    files: ['public/*.html', 'cua/static/*.html'],
    plugins: { html },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'script',
      globals: browserGlobals,
    },
    rules: {
      'no-unused-vars': 'off',   // onclick/onchange attrs are invisible to ESLint
      'no-undef': 'error',
      'no-console': 'off',
      'eqeqeq': 'error',
      'no-eval': 'error',
    },
  },
];
