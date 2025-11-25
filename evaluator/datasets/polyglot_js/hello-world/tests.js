import { describe, expect, test } from '@jest/globals';
import { hello } from './main';

describe('Hello World', () => {
  test('Say Hi!', () => {
    expect(hello()).toEqual('Hello, World!');
  });
});
