//
// This is only a SKELETON file for the 'Bank Account' exercise. It's been provided as a
// convenience to get you started writing code faster.
//

export class BankAccount {
  constructor() {
    throw new Error('Remove this line and implement the function');
  }

  open() {
    throw new Error('Remove this line and implement the function');
  }

  close() {
    throw new Error('Remove this line and implement the function');
  }

  /**
   * @param {number} amount
   */
  deposit(amount) {
    throw new Error('Remove this line and implement the function');
  }

  /**
   * @param {number} amount
   */
  withdraw(amount) {
    throw new Error('Remove this line and implement the function');
  }

  /**
   * @return {number}
   */
  get balance() {
    throw new Error('Remove this line and implement the function');
  }
}

export class ValueError extends Error {
  constructor() {
    super('Bank account error');
  }
}
