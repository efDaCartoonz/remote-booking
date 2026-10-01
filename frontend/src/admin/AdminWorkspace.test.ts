import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminWorkspace from "./AdminWorkspace.vue";

const originalFetch = globalThis.fetch;

const ok = (body: unknown): Response =>
  ({
    ok: true,
    status: 200,
    json: async () => body,
  } as Response);

const fail = (status: number, detail: unknown): Response =>
  ({
    ok: false,
    status,
    json: async () => ({ detail }),
  } as Response);

const sampleUsers = [
  {
    id: 1,
    username: "admin_user",
    full_name: "Главный Администратор",
    email: "admin@example.com",
    phone: "+79990000000",
    omnidesk_staff_id: "101",
    is_active: true,
    roles: [{ id: 4, name: "Администратор" }],
    timezone: "Europe/Moscow",
    telegram_chat_id: "tg_admin",
    bitrix24_user_id: "b24_admin",
  },
  {
    id: 2,
    username: "engineer_l2",
    full_name: "Инженер Второй Линии",
    email: "l2@example.com",
    phone: "+79991112233",
    omnidesk_staff_id: "102",
    is_active: true,
    roles: [{ id: 2, name: "Инженер L2" }],
    timezone: "Asia/Yekaterinburg",
    telegram_chat_id: "tg_l2",
    bitrix24_user_id: "b24_l2",
  },
];

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("FE-04 AdminWorkspace", () => {
  it("renders nothing and makes no API calls when user is not ADMIN", async () => {
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock;

    // Test with L1 user
    const wrapperL1 = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 10, username: "l1_user", roles: [1] },
      },
    });
    await flushPromises();
    expect(wrapperL1.find(".admin-workspace").exists()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(0);

    // Test with Manager user
    const wrapperManager = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 11, username: "manager_user", roles: [{ id: 3, name: "Руководитель" }] },
      },
    });
    await flushPromises();
    expect(wrapperManager.find(".admin-workspace").exists()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(0);

    // Test with null user
    const wrapperNull = mount(AdminWorkspace, {
      props: { currentUser: null },
    });
    await flushPromises();
    expect(wrapperNull.find(".admin-workspace").exists()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(0);
  });

  it("renders admin workspace and loads users list for ADMIN role", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/admin/users")) {
        return ok(sampleUsers);
      }
      return fail(404, "not_found");
    });

    const wrapper = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin_user", roles: [4] },
        timezone: "Asia/Yekaterinburg",
      },
    });
    await flushPromises();

    expect(wrapper.find(".admin-workspace").exists()).toBe(true);
    expect(wrapper.text()).toContain("Панель администратора");
    expect(wrapper.text()).toContain("Главный Администратор");
    expect(wrapper.text()).toContain("engineer_l2");
  });

  it("creates user without leaking password in DOM after submission", async () => {
    const fetchCalls: { url: string; body?: unknown }[] = [];
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      fetchCalls.push({ url, body });

      if (url === "/api/v1/admin/users" && init?.method === "POST") {
        return ok({
          id: 3,
          username: body.username,
          full_name: body.full_name,
          email: body.email,
          phone: body.phone,
          omnidesk_staff_id: body.omnidesk_staff_id,
          is_active: true,
          roles: [{ id: 2, name: "Инженер L2" }],
          timezone: "Asia/Yekaterinburg",
        });
      }
      if (url.includes("/api/v1/admin/users")) {
        return ok(sampleUsers);
      }
      return fail(404, "not_found");
    });

    const wrapper = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin_user", roles: [{ id: 4, name: "Admin" }] },
      },
    });
    await flushPromises();

    // Open create user modal
    await wrapper.find('[data-test="btn-open-create-user"]').trigger("click");
    expect(wrapper.find('[data-test="modal-create-user"]').exists()).toBe(true);

    const secretPassword = "SuperSecretPassword123!";

    // Fill form
    await wrapper.find('[data-test="input-new-username"]').setValue("new_engineer");
    await wrapper.find('[data-test="input-new-password"]').setValue(secretPassword);
    await wrapper.find('[data-test="input-new-fullname"]').setValue("Новый Инженер");
    await wrapper.find('[data-test="input-new-email"]').setValue("new@test.com");

    // Submit form
    await wrapper.find('[data-test="modal-create-user"] form').trigger("submit.prevent");
    await flushPromises();

    // Verify API received the password
    const postCall = fetchCalls.find((c) => c.url === "/api/v1/admin/users" && c.body);
    expect(postCall).toBeDefined();
    expect((postCall?.body as Record<string, unknown>).password).toBe(secretPassword);

    // CRITICAL: Verify password is NOT present anywhere in wrapper HTML / DOM after submit
    expect(wrapper.html()).not.toContain(secretPassword);
    expect(wrapper.text()).not.toContain(secretPassword);
    expect(wrapper.text()).toContain("Пользователь new_engineer успешно создан.");
  });

  it("requires mandatory reason for distribution membership updates", async () => {
    let updateCallMade = false;
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/admin/users")) {
        return ok(sampleUsers);
      }
      if (url.includes("/api/v1/admin/distribution/members") && init?.method === "PUT") {
        updateCallMade = true;
        return ok({
          id: 1,
          user_id: 2,
          pool_code: 2,
          is_enabled: false,
          comment: "Решение руководителя №42",
        });
      }
      if (url.includes("/api/v1/admin/distribution/members")) {
        return ok([
          {
            id: 1,
            user_id: 2,
            pool_code: 2,
            is_enabled: true,
            comment: "Начальное включение",
          },
        ]);
      }
      return fail(404, "not_found");
    });

    const wrapper = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin_user", roles: [4] },
      },
    });
    await flushPromises();

    // Switch to Schedules tab
    await wrapper.find('[data-test="tab-schedules"]').trigger("click");
    await flushPromises();

    // Switch to Distribution subtab
    await wrapper.find('[data-test="subtab-distribution"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="table-distribution"]').exists()).toBe(true);

    // Click exclude button
    await wrapper.find('[data-test="btn-toggle-dist-2-2"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="panel-edit-distribution"]').exists()).toBe(true);

    // Try submit with empty comment
    await wrapper.find('[data-test="textarea-dist-comment"]').setValue("   ");
    await wrapper.find('[data-test="panel-edit-distribution"] form').trigger("submit.prevent");
    await flushPromises();

    expect(updateCallMade).toBe(false);
    expect(wrapper.text()).toContain("Укажите обязательное основание (решение руководителя).");

    // Submit with non-empty comment
    await wrapper.find('[data-test="textarea-dist-comment"]').setValue("Решение руководителя №42");
    await wrapper.find('[data-test="panel-edit-distribution"] form').trigger("submit.prevent");
    await flushPromises();

    expect(updateCallMade).toBe(true);
    expect(wrapper.text()).toContain("Участие сотрудника в распределении обновлено.");
  });

  it("handles and displays 409 conflict and 422 errors properly", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/api/v1/admin/users") && init?.method === "POST") {
        return fail(409, "username_already_exists");
      }
      if (url.includes("/api/v1/admin/users")) {
        return ok(sampleUsers);
      }
      return fail(404, "not_found");
    });

    const wrapper = mount(AdminWorkspace, {
      props: {
        currentUser: { id: 1, username: "admin_user", roles: [4] },
      },
    });
    await flushPromises();

    // Open create user
    await wrapper.find('[data-test="btn-open-create-user"]').trigger("click");
    await wrapper.find('[data-test="input-new-username"]').setValue("admin_user");
    await wrapper.find('[data-test="input-new-password"]').setValue("pwd");
    await wrapper.find('[data-test="input-new-fullname"]').setValue("Duplicate User");
    await wrapper.find('[data-test="modal-create-user"] form').trigger("submit.prevent");
    await flushPromises();

    expect(wrapper.find('[data-test="admin-error"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="admin-error"]').text()).toContain(
      "Пользователь с таким логином уже существует."
    );
  });
});
