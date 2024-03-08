class PTAccordion {
    constructor(containerId, jsonData) {
      this.container = document.getElementById(containerId);
      this.sections = [];
  
      // Parse JSON data and add sections
      if (jsonData && Array.isArray(jsonData)) {
        jsonData.forEach(item => {
          this.addSection(item.id, item.header, item.content);
        });
      }
    }
  
    addSection(id, headerText, contentText) {
      const section = document.createElement('div');
      section.classList.add('pt-accordion-item');
      section.setAttribute('data-id', id);
  
      const header = document.createElement('div');
      header.classList.add('pt-accordion-header');
      header.textContent = headerText;
      header.style.cursor = 'pointer';
      header.addEventListener('click', () => {
        content.style.display = content.style.display === 'none' ? 'block' : 'none';
        this._fireEvent('click', { id }); // Fire click event
      });
  
      const content = document.createElement('div');
      content.classList.add('pt-accordion-content');
      content.textContent = contentText;
      content.style.display = 'none';
  
      section.appendChild(header);
      section.appendChild(content);
  
      this.sections.push(section);
    }
  
    render() {
      this.sections.forEach(section => {
        this.container.appendChild(section);
      });
    }
  
    collapse(id) {
      if (id) {
        const section = this.container.querySelector(`[data-id="${id}"]`);
        if (section) {
          const content = section.querySelector('.pt-accordion-content');
          content.style.display = 'none';
          this._fireEvent('afterCollapse', { id }); // Fire afterCollapse event
        }
      } else {
        this.sections.forEach(section => {
          const content = section.querySelector('.pt-accordion-content');
          content.style.display = 'none';
          this._fireEvent('afterCollapse', { id: section.dataset.id }); // Fire afterCollapse event for all sections
        });
      }
    }
  
    expand(id) {
      if (id) {
        const section = this.container.querySelector(`[data-id="${id}"]`);
        if (section) {
          const content = section.querySelector('.pt-accordion-content');
          content.style.display = 'block';
          this._fireEvent('afterExpand', { id }); // Fire afterExpand event
        }
      } else {
        this.sections.forEach(section => {
          const content = section.querySelector('.pt-accordion-content');
          content.style.display = 'block';
          this._fireEvent('afterExpand', { id: section.dataset.id }); // Fire afterExpand event for all sections
        });
      }
    }
  
    // Internal method to fire events
    _fireEvent(eventName, eventData) {
      const event = new CustomEvent(eventName, { detail: eventData });
      this.container.dispatchEvent(event);
    }
  
    // Other methods and properties can be added as needed...
  }