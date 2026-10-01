import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AppSidebar from "./AppSidebar.vue";

const labels = (roles: number[], path = "/work") =>
  mount(AppSidebar, { props: { roles, fullName: "Тест", path } })
    .findAll("a")
    .map((a) => a.text());

describe("AppSidebar", () => {
  it("shows only the sections of the user's roles", () => {
    expect(labels([1])).toEqual(["Мои карточки", "Профиль"]);
    expect(labels([3])).toEqual(["Панель руководителя", "Графики работы", "Отчёты", "Профиль"]);
    expect(labels([4])).toEqual(["Графики работы", "Отчёты", "Администрирование", "Профиль"]);
    expect(labels([2, 3])).toContain("Мои карточки");
  });

  it("marks the current section and emits logout", async () => {
    const wrapper = mount(AppSidebar, { props: { roles: [3], fullName: "Руководитель", path: "/schedules" } });
    expect(wrapper.find("a.active").text()).toBe("Графики работы");
    await wrapper.find('[data-test="sidebar-logout"]').trigger("click");
    expect(wrapper.emitted("logout")).toHaveLength(1);
  });

  it("treats the root path as the work list", () => {
    const wrapper = mount(AppSidebar, { props: { roles: [2], fullName: "L2", path: "/" } });
    expect(wrapper.find("a.active").text()).toBe("Мои карточки");
  });
});
