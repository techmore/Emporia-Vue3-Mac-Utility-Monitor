// Execute the rendered page's actual submit handler with controlled responses.
const fs = require('fs');
const vm = require('vm');

async function run(input) {
  let submit;
  const button = {disabled: false, textContent: 'Import'};
  const results = {innerHTML: ''};
  const files = input.responses.map((response, index) => ({
    name: response.filename || `panel-${index}-1H.csv`,
  }));
  const elements = {
    'import-form': {addEventListener: (name, callback) => {
      if (name === 'submit') submit = callback;
    }},
    'csv-files': {files},
    'import-btn': button,
    'import-results': results,
  };
  let calls = 0;
  const context = {
    document: {
      getElementById: id => elements[id],
      createElement: () => ({
        textContent: '',
        get innerHTML() {
          return this.textContent.replace(/&/g, '&amp;')
            .replace(/</g, '&lt;').replace(/>/g, '&gt;');
        },
      }),
    },
    FormData: class {append() {}},
    fetch: async (url, options) => {
      if (url !== '/api/import-csv' || options.method !== 'POST') {
        throw new Error('Unexpected request');
      }
      const response = input.responses[calls++];
      if (response.networkError) throw new Error(response.networkError);
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        json: async () => {
          if (response.invalidJSON) throw new SyntaxError('Unexpected token <');
          return response.body;
        },
      };
    },
  };
  context.window = context;
  vm.runInNewContext(input.escapeScript + '\n' + input.script, context);
  await submit({preventDefault() {}});
  return {html: results.innerHTML, calls, ...button};
}

run(JSON.parse(fs.readFileSync(0, 'utf8')))
  .then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => {console.error(error); process.exitCode = 1;});
