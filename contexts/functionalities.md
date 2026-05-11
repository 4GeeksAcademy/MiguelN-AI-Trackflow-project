# Functionalities Specification

> Source: Email from Tech Lead — *Core functionalities for data processing*.
> Purpose: Reference document to guide implementation (intended to be consumed by Copilot as context).

---

## 1. Project Overview

Implement a set of **TypeScript** functions to efficiently handle the company's main data.

**Goals:**
- Deliver solid, well-typed utilities.
- Make them reusable across multiple contexts.
- Keep the codebase maintainable in the long term.

**Tech stack:** TypeScript.

---

## 2. Core Requirements

The implementation is divided into **four functional modules**. Each module groups related responsibilities.

### 2.1. Collection Management System

Functions that operate over arrays (collections of elements).

**Required operations:**
- **Filter** — select elements matching a given criterion.
- **Sort** — order elements based on a key/comparator.
- **Search** — find a specific element. Two variants are required:
  - **Linear search** → for **unsorted** arrays.
  - **Binary search** → for **sorted** arrays.
- **Group** — cluster elements by a given attribute/criterion.

**Edge cases that must be properly handled:**
- **Empty collections** — functions must not throw; they must return a predictable value (e.g., empty array, `null`, `undefined`, etc., depending on the operation).
- **Element not found** — search functions must explicitly handle the not-found case (no crashes, no silent errors).

---

### 2.2. Data Modeling with Objects and Interfaces

Define the TypeScript interfaces that represent the **main business entities**.

**Rules:**
- Every interface must declare **explicit types** for **all** its properties (no implicit `any`).
- Every interface must expose **auxiliary methods** to operate on its data.
- Concrete instances of these entities must be represented using **literal objects**.

> The exact entities to model are defined in the company's context document (see section 4).

---

### 2.3. Transformations and Aggregations

Functions that take collections of objects and produce **simple reports**.

**Required operations:**
- **Count** elements by category.
- **Sum** numeric values.
- **Find maximum** values.
- **Find minimum** values.
- **Calculate averages**.

**Rules:**
- All inputs and outputs must be **fully typed**.
- Each operation should be exposed as a standalone function.

> The exact reports to generate are defined in the company's context document (see section 4).

---

### 2.4. Business Validations

Functions that validate data against the company's specific business rules **before** it is processed or stored.

**Required validation categories:**
- **Required fields** — verify that the element contains all mandatory properties.
- **Numeric ranges** — verify that numeric values fall within the allowed range.
- **Date coherence** — verify that dates are logically consistent (correct order, valid values, etc.).

> The exact validation rules are defined in the company's context document (see section 4).

---

## 3. Coding Rules and Standards

These rules apply to **every** function and module produced.

| Rule | Description |
|------|-------------|
| Clean code | Readable, self-explanatory, no unnecessary complexity. |
| Descriptive names | Variables, functions, types and interfaces must clearly express their intent. |
| Single Responsibility | Each function does **one** thing only. |
| Long-term maintainability | Structure code so it can be safely extended and modified later. |
| Strong typing | Leverage TypeScript fully — no implicit `any`, no untyped parameters or return values. |
| Reusability | Utilities must be context-independent and reusable across the codebase. |

---

## 4. External Dependency: Company Context Document

The company's context document is **required** to complete the implementation. It specifies:

- **Which entities** must be modeled (section 2.2).
- **Which validations** must be applied (section 2.4).
- **Which reports** must be generated (section 2.3).

> ⚠️ Without this document, the abstract structure can be implemented, but business-specific logic cannot be finalized.

---

## 5. Deliverables Summary

| Module | Responsibility |
|--------|----------------|
| **Collections** | Filter, sort, group, linear search (unsorted), binary search (sorted), with safe handling of empty arrays and not-found cases. |
| **Models** | TypeScript interfaces with explicit property types and auxiliary methods; literal objects for concrete instances. |
| **Reports** | Typed aggregation functions: count by category, sum, min, max, average. |
| **Validations** | Functions for required fields, numeric range checks, and date coherence checks. |

---

## 6. Definition of Done

A functionality is considered complete when:

- [ ] It is implemented in TypeScript with full, explicit typing.
- [ ] It follows the Single Responsibility Principle.
- [ ] It uses descriptive names.
- [ ] It handles edge cases (empty collections, not-found elements, invalid data).
- [ ] It is reusable and decoupled from any specific context.
- [ ] It conforms to the entity definitions, validation rules and report specifications described in the company's context document.
