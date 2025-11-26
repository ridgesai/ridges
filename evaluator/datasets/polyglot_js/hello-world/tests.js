
import { hello } from './main.js';

describe('Hello World', () => {
  test('Say Hi!', () => {
    expect(hello()).toEqual('Hello, World!');
  });
});
