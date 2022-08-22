$( document ).ready(function() {
    var custom_fields = $('#custom_fields-0');
    var fields_template = custom_fields.html();
    custom_fields.hide();

    $('#add_field').click(function(){
        var new_id = $('.fieldref').length;
        var last_id = new_id - 1;
        var new_element = fields_template.replace(/custom_fields-0/g, 'custom_fields-'+new_id);

       $('#custom_fields').append('<div class="fieldref" id="custom_fields-'+new_id+'">'+new_element+'</div>');

    });

    var tickets = $('#tickets-0');
    var ticket_template = tickets.html();
    tickets.hide();

    $('#add_ticket').click(function(){
        var new_id = $('.ticketref').length;
        var last_id = new_id - 1;
        var new_element = ticket_template.replace(/tickets-0/g, 'ticketss-'+new_id);

       $('#tickets').append('<div class="ticketref" id="tickets-'+new_id+'">'+new_element+'</div>');

    });
});
